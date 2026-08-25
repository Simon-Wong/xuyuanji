"""模型封装统一实现：chat（流式/非流式 + function calling）+ embed。"""
from __future__ import annotations

import json
from typing import Any, Iterator

from agent.model_wrapper.client import OpenAIClient
from agent.model_wrapper.tools import ToolRegistry
from agent.model_wrapper.data_types import ChatChunk, ChatResult, EmbedResult, ToolCall


class OpenAIModelWrapper:
    """模型封装统一实现。

    system prompt 拼装（顺序）：
        静态部分（配置读） -> 动态部分（调用方传），拼接后插入 messages 开头。

    function calling：自动循环执行，auto_execute_tools=False 时返回 tool_calls
    等待调用方授权。
    """

    def __init__(
        self,
        client: Any,
        *,
        model_key: str = "default",
        tool_registry: ToolRegistry | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._client = client
        self._model_key = model_key
        self._tool_registry = tool_registry

        cfg = config or {}
        self._auto_execute = cfg.get("auto_execute_tools", True)
        self._max_tool_calls = cfg.get("max_tool_calls", 10)
        self._max_retries = cfg.get("max_retries", 3)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system_prompt_static: str | None = None,
        system_prompt_dynamic: str | None = None,
        tools: list[dict] | None = None,
        stream: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        trace_id: str | None = None,
    ) -> ChatResult | Iterator[ChatChunk]:
        """对话接口。

        system_prompt 拼装：静态 + 动态，先静态后动态，插入 messages 开头。
        stream=True 返回 Iterator[ChatChunk]，否则返回 ChatResult。
        """
        # 1. 拼装 system prompt
        sp = ""
        if system_prompt_static:
            sp += system_prompt_static
        if system_prompt_dynamic:
            sp += ("\n" + system_prompt_dynamic) if sp else system_prompt_dynamic
        if sp:
            messages = [{"role": "system", "content": sp}] + messages

        # 2. 合并 tools（内置 + 调用方传入）
        all_tools: list[dict] = []
        if self._tool_registry is not None:
            all_tools.extend(self._tool_registry.get_schemas())
        if tools:
            all_tools.extend(tools)

        # 3. 调用
        if stream:
            return self._chat_stream(messages, all_tools, temperature, max_tokens, trace_id)
        return self._chat_sync(messages, all_tools, temperature, max_tokens, trace_id)

    def embed(
        self,
        texts: list[str],
        *,
        trace_id: str | None = None,
    ) -> list[EmbedResult]:
        """嵌入向量接口。"""
        response = self._client.embed(self._model_key, texts)
        results = []
        for data, usage in zip(response.data, _iter_usage(response)):
            results.append(EmbedResult(
                embedding=list(data.embedding),
                model=response.model,
                usage=usage,
            ))
        return results

    # ------------------------------------------------------------------
    # 非流式
    # ------------------------------------------------------------------
    def _chat_sync(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict],
        temperature: float | None,
        max_tokens: int | None,
        trace_id: str | None,
    ) -> ChatResult:
        all_tool_calls: list[ToolCall] = []

        while True:
            response = self._client.chat(
                self._model_key,
                messages,
                tools=tools or None,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
            msg = response.choices[0].message
            content = getattr(msg, "content", None) or ""
            tool_calls = _extract_tool_calls(msg)
            usage = _extract_usage(response)

            if not tool_calls:
                return ChatResult(content=content, tool_calls=all_tool_calls, usage=usage)

            if not self._auto_execute:
                return ChatResult(content=content, tool_calls=tool_calls, usage=usage)

            # 自动执行
            all_tool_calls.extend(tool_calls)
            messages.append(_assistant_message(content, tool_calls))
            for tc in tool_calls:
                messages.append(self._run_tool(tc))

            if len(all_tool_calls) >= self._max_tool_calls:
                return ChatResult(content=content, tool_calls=all_tool_calls, usage=usage)

    # ------------------------------------------------------------------
    # 流式
    # ------------------------------------------------------------------
    def _chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict],
        temperature: float | None,
        max_tokens: int | None,
        trace_id: str | None,
    ) -> Iterator[ChatChunk]:
        total_calls = 0

        while True:
            response = self._client.chat(
                self._model_key,
                messages,
                tools=tools or None,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            content_parts: list[str] = []
            tool_map: dict[int, dict[str, str]] = {}

            for chunk in response:
                delta = _chunk_delta(chunk)
                if delta is None:
                    continue

                dcontent = getattr(delta, "content", None)
                if dcontent:
                    content_parts.append(dcontent)
                    yield ChatChunk(delta_content=dcontent)

                for tc_delta in (getattr(delta, "tool_calls", None) or []):
                    _accumulate_tool_delta(tc_delta, tool_map)

            tool_calls = _assemble_tool_calls(tool_map)
            content = "".join(content_parts)

            if not tool_calls:
                yield ChatChunk(done=True)
                return

            if not self._auto_execute:
                yield ChatChunk(tool_calls=tool_calls, done=True)
                return

            # 自动执行
            total_calls += len(tool_calls)
            messages.append(_assistant_message(content, tool_calls))
            for tc in tool_calls:
                yield ChatChunk(tool_name=tc.name, tool_calls=[tc])
                result = self._run_tool_content(tc)
                yield ChatChunk(tool_name=tc.name, tool_result=result)
                messages.append(_tool_message(tc, result))

            if total_calls >= self._max_tool_calls:
                yield ChatChunk(done=True)
                return

    # ------------------------------------------------------------------
    # 工具执行
    # ------------------------------------------------------------------
    def _run_tool(self, tc: ToolCall) -> dict[str, Any]:
        return _tool_message(tc, self._run_tool_content(tc))

    def _run_tool_content(self, tc: ToolCall) -> str:
        if self._tool_registry is None:
            return json.dumps({"error": "无 ToolRegistry"}, ensure_ascii=False)
        for _ in range(max(1, self._max_retries)):
            result = self._tool_registry.execute(tc)
            if not result.startswith('{"error"'):
                return result
        return self._tool_registry.execute(tc)


# ==============================================================================
# 辅助函数：从 OpenAI 响应提取 / 组装
# ==============================================================================
def _extract_tool_calls(msg: Any) -> list[ToolCall]:
    result: list[ToolCall] = []
    for tc in (getattr(msg, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        result.append(ToolCall(
            id=getattr(tc, "id", "") or "",
            name=getattr(fn, "name", "") or "",
            arguments=getattr(fn, "arguments", "") or "",
        ))
    return result


def _extract_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
    }


def _iter_usage(response: Any) -> Iterator[dict[str, int]]:
    usage = _extract_usage(response)
    while True:
        yield usage


def _chunk_delta(chunk: Any) -> Any:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    return getattr(choices[0], "delta", None)


def _accumulate_tool_delta(tc_delta: Any, tool_map: dict[int, dict[str, str]]) -> None:
    idx = getattr(tc_delta, "index", 0)
    if idx not in tool_map:
        tool_map[idx] = {"id": "", "name": "", "arguments": ""}
    if getattr(tc_delta, "id", None):
        tool_map[idx]["id"] = tc_delta.id
    fn = getattr(tc_delta, "function", None)
    if fn is not None:
        if getattr(fn, "name", None):
            tool_map[idx]["name"] += fn.name
        if getattr(fn, "arguments", None):
            tool_map[idx]["arguments"] += fn.arguments


def _assemble_tool_calls(tool_map: dict[int, dict[str, str]]) -> list[ToolCall]:
    result: list[ToolCall] = []
    for idx in sorted(tool_map):
        info = tool_map[idx]
        result.append(ToolCall(
            id=info["id"],
            name=info["name"],
            arguments=info["arguments"],
        ))
    return result


def _to_openai_tool_calls(tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.name, "arguments": tc.arguments},
        }
        for tc in tool_calls
    ]


def _assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        msg["tool_calls"] = _to_openai_tool_calls(tool_calls)
    return msg


def _tool_message(tc: ToolCall, result: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tc.id, "content": result}


# ==============================================================================
# 测试入口
# ==============================================================================
if __name__ == "__main__":
    """验收测试 T1-T22"""
    import os as _os
    import sys as _sys
    from types import SimpleNamespace as _NS

    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    if _ROOT not in _sys.path:
        _sys.path.insert(0, _ROOT)

    from pydantic import BaseModel, Field

    from agent.model_wrapper.client import OpenAIClient
    from agent.model_wrapper.tools import ToolRegistry, tool
    from agent.model_wrapper.data_types import ChatResult, ChatChunk, ToolCall, EmbedResult

    passed = 0
    failed = 0

    def check(name, cond, msg=""):
        global passed, failed
        if cond:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}  {msg}")

    # ===== T1-T5: @tool 装饰器（复用 openai-agents function_tool）=====
    print("=== T1-T5: @tool 装饰器 ===")

    @tool
    def get_weather(city: str) -> str:
        """查询指定城市的天气。

        Args:
            city: 城市名称，如"上海"
        """
        return f"{city}今天晴朗"

    check("T1a", get_weather.name == "get_weather")
    check("T1b", "查询指定城市的天气" in (get_weather.description or ""),
          f"description={get_weather.description!r}")
    check("T1c", get_weather.params_json_schema["properties"]["city"]["type"] == "string")

    # T2: docstring 参数描述解析
    city_desc = get_weather.params_json_schema["properties"]["city"].get("description", "")
    check("T2", "城市名称" in city_desc, f"city 描述: {city_desc}")

    # T3: Pydantic BaseModel 参数（function_tool 原生支持，对标 langchain args_schema）
    class SearchInput(BaseModel):
        query: str = Field(description="搜索关键词")
        limit: int = Field(default=10, description="最大返回数量")

    @tool
    def search_database(input: SearchInput) -> str:
        """搜索客户数据库。"""
        return f"找到 {input.limit} 条结果"

    s_schema = search_database.params_json_schema
    s_defs = s_schema.get("$defs", {})
    s_props = s_defs.get("SearchInput", {}).get("properties", {})
    check("T3a", s_props.get("query", {}).get("description") == "搜索关键词",
          f"query: {s_props.get('query')}")
    check("T3b", s_props.get("limit", {}).get("type") == "integer")
    check("T3c", "query" in s_defs.get("SearchInput", {}).get("required", []),
          "query 应为 required")

    # T4: 自定义 name/description 覆盖
    @tool(name_override="weather_lookup", description_override="自定义描述")
    def some_fn(city: str) -> str:
        return city

    check("T4a", some_fn.name == "weather_lookup")
    check("T4b", some_fn.description == "自定义描述")

    # T5: 必填参数标记 required（strict_mode=False，无默认值参数才 required）
    @tool(strict_mode=False)
    def multi_arg(a: str, b: int = 5, c: bool = False) -> str:
        """多参数函数。"""
        return f"{a}{b}{c}"

    m_required = multi_arg.params_json_schema.get("required", [])
    check("T5", m_required == ["a"], f"required={m_required}")

    # ===== T6-T9: ToolRegistry =====
    print("=== T6-T9: ToolRegistry ===")

    registry = ToolRegistry()
    tid = registry.register(get_weather)
    check("T6a", tid == "get_weather")
    schemas = registry.get_schemas()
    check("T6b", len(schemas) == 1 and schemas[0]["function"]["name"] == "get_weather")

    # T7: execute 执行函数
    tc = ToolCall(id="call_1", name="get_weather", arguments='{"city": "北京"}')
    result = registry.execute(tc)
    check("T7", result == "北京今天晴朗", f"result={result}")

    # T8: 参数解析正确（多参数）
    registry.register(multi_arg)
    tc2 = ToolCall(id="call_2", name="multi_arg", arguments='{"a": "x", "b": 3}')
    result2 = registry.execute(tc2)
    check("T8", result2 == "x3False", f"result2={result2}")

    # T9: remove 移除工具
    registry.remove("get_weather")
    names = registry.list_tools()
    check("T9", "get_weather" not in names, f"names={names}")

    # ===== T10-T11: OpenAIClient =====
    print("=== T10-T11: OpenAIClient ===")

    providers = {
        "Commercial": {"base_url": "https://dashscope.example/v1", "api_key_env": "TEST_KEY"},
        "Ollama": {"base_url": "http://localhost:11434/v1", "api_key_env": None},
    }
    models = {
        "default": {"provider": "Commercial", "model": "qwen-plus", "type": "chat"},
        "local-chat": {"provider": "Ollama", "model": "qwen2.5:7b", "type": "chat"},
    }

    _os.environ["TEST_KEY"] = "test-key"
    client = OpenAIClient(providers, models)
    c1, m1 = client.get_client("default")
    c2, m2 = client.get_client("local-chat")
    check("T10a", c1 is not c2, "不同 provider 应返回不同 client")
    check("T10b", m1 == "qwen-plus" and m2 == "qwen2.5:7b")

    # T11: 无 api_key_env 正常（本地模型）
    # Ollama 已在上方创建，若未抛异常即通过
    check("T11", True)

    # ===== T12-T15: ModelWrapper 非流式 =====
    print("=== T12-T15: ModelWrapper 非流式 ===")

    class FakeClient:
        """模拟 OpenAI 客户端（无网络）。"""
        def __init__(self, scripts, default_text="完成"):
            # scripts: 按调用次数返回不同响应的队列
            self.scripts = scripts
            self.calls = []  # 记录每次 chat 的 messages/tools
            self.default_text = default_text

        def chat(self, model_key, messages, tools=None, temperature=None,
                 max_tokens=None, stream=False):
            self.calls.append((model_key, messages, tools))
            resp = self.scripts.pop(0)
            if stream:
                return resp
            return resp

        def embed(self, model_key, texts):
            data = [_NS(embedding=[0.1, 0.2, 0.3]) for _ in texts]
            return _NS(data=data, model="embed-model",
                       usage=_NS(prompt_tokens=10, completion_tokens=0, total_tokens=10))

    def make_text_response(text, usage=None):
        usage = usage or _NS(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        msg = _NS(content=text, tool_calls=None)
        choice = _NS(message=msg)
        return _NS(choices=[choice], usage=usage)

    def make_tool_response(content, tool_calls):
        msg = _NS(content=content, tool_calls=[
            _NS(id=tc.id, function=_NS(name=tc.name, arguments=tc.arguments))
            for tc in tool_calls
        ])
        choice = _NS(message=msg)
        return _NS(choices=[choice], usage=_NS(prompt_tokens=10, completion_tokens=5, total_tokens=15))

    # T12: 非流式 chat 返回 ChatResult
    fake = FakeClient([make_text_response("你好")])
    wrapper = OpenAIModelWrapper(fake, tool_registry=None, config={})
    r = wrapper.chat([{"role": "user", "content": "hi"}], system_prompt_static="你是助手")
    check("T12a", isinstance(r, ChatResult), f"应返回 ChatResult，实际 {type(r)}")
    check("T12b", r.content == "你好")
    check("T12c", r.usage.get("total_tokens") == 15)

    # T13: system prompt 拼装（静态 + 动态 + 插入开头）
    fake2 = FakeClient([make_text_response("ok")])
    wrapper2 = OpenAIModelWrapper(fake2, config={})
    wrapper2.chat(
        [{"role": "user", "content": "hi"}],
        system_prompt_static="静态",
        system_prompt_dynamic="动态",
    )
    sent_messages = fake2.calls[0][1]
    check("T13a", sent_messages[0]["role"] == "system")
    check("T13b", sent_messages[0]["content"] == "静态\n动态",
          f"system content: {sent_messages[0]['content']!r}")
    check("T13c", sent_messages[-1] == {"role": "user", "content": "hi"})

    # T14: 非流式 + 函数调用自动循环
    reg = ToolRegistry()
    reg.register(get_weather)
    script = [
        make_tool_response("让我查天气", [ToolCall(id="c1", name="get_weather", arguments='{"city":"上海"}')]),
        make_text_response("上海今天晴朗"),
    ]
    fake3 = FakeClient(script)
    wrapper3 = OpenAIModelWrapper(fake3, tool_registry=reg, config={"auto_execute_tools": True})
    r3 = wrapper3.chat([{"role": "user", "content": "上海天气如何"}])
    check("T14a", r3.content == "上海今天晴朗")
    check("T14b", len(r3.tool_calls) == 1 and r3.tool_calls[0].name == "get_weather")
    check("T14c", len(fake3.calls) == 2, f"应调 2 次 LLM，实际 {len(fake3.calls)}")

    # T15: 非流式 + auto_execute=False 返回 tool_calls 不自动执行
    script = [
        make_tool_response("让我查天气", [ToolCall(id="c1", name="get_weather", arguments='{"city":"上海"}')]),
    ]
    fake4 = FakeClient(script)
    wrapper4 = OpenAIModelWrapper(fake4, tool_registry=reg, config={"auto_execute_tools": False})
    r4 = wrapper4.chat([{"role": "user", "content": "上海天气如何"}])
    check("T15a", len(r4.tool_calls) == 1)
    check("T15b", len(fake4.calls) == 1, f"不应自动循环，实际 {len(fake4.calls)}")

    # ===== T16-T19: ModelWrapper 流式 =====
    print("=== T16-T19: ModelWrapper 流式 ===")

    def make_stream(chunks):
        """构造流式响应（可迭代）。"""
        return chunks

    def delta_chunk(content=None, tool_deltas=None):
        delta = _NS(content=content, tool_calls=tool_deltas or [])
        return _NS(choices=[_NS(delta=delta)])

    # T16: 流式返回 Iterator[ChatChunk]，逐个 yield delta_content
    stream1 = make_stream([
        delta_chunk(content="你"),
        delta_chunk(content="好"),
    ])
    fake5 = FakeClient([stream1])
    wrapper5 = OpenAIModelWrapper(fake5, config={})
    gen = wrapper5.chat([{"role": "user", "content": "hi"}], stream=True)
    chunks = list(gen)
    texts = [c.delta_content for c in chunks if c.delta_content]
    check("T16a", texts == ["你", "好"], f"texts={texts}")
    check("T16b", chunks[-1].done is True)

    # T17: 流式 + 函数调用自动循环，yield tool_call + tool_result
    def td_chunk(index, id=None, name=None, args=None):
        fn = _NS(name=name, arguments=args)
        return _NS(index=index, id=id, function=fn)

    stream2 = make_stream([
        delta_chunk(content="让我查"),
        delta_chunk(tool_deltas=[td_chunk(0, id="c1", name="get_weather", args='{"city":')]),
        delta_chunk(tool_deltas=[td_chunk(0, name="", args='"上海"}')]),
    ])
    stream3 = make_stream([delta_chunk(content="上海今天晴朗")])
    fake6 = FakeClient([stream2, stream3])
    wrapper6 = OpenAIModelWrapper(fake6, tool_registry=reg, config={"auto_execute_tools": True})
    chunks6 = list(wrapper6.chat([{"role": "user", "content": "上海天气"}], stream=True))

    tool_names = [c.tool_name for c in chunks6 if c.tool_calls]
    tool_results = [c.tool_result for c in chunks6 if c.tool_result]
    final_texts = [c.delta_content for c in chunks6 if c.delta_content]
    check("T17a", tool_names == ["get_weather"], f"tool_names={tool_names}")
    check("T17b", tool_results == ["上海今天晴朗"], f"tool_results={tool_results}")
    check("T17c", final_texts == ["让我查", "上海今天晴朗"], f"final_texts={final_texts}")

    # T18: 流式 + auto_execute=False，最后 yield 完整 tool_calls done=True
    stream4 = make_stream([
        delta_chunk(content="让我查"),
        delta_chunk(tool_deltas=[td_chunk(0, id="c1", name="get_weather", args='{"city":"上海"}')]),
    ])
    fake7 = FakeClient([stream4])
    wrapper7 = OpenAIModelWrapper(fake7, tool_registry=reg, config={"auto_execute_tools": False})
    chunks7 = list(wrapper7.chat([{"role": "user", "content": "上海天气"}], stream=True))
    last = chunks7[-1]
    check("T18a", last.done is True)
    check("T18b", last.tool_calls is not None and last.tool_calls[0].name == "get_weather")
    check("T18c", last.tool_calls[0].arguments == '{"city":"上海"}')

    # T19: max_tool_calls 限制
    # 构造连续 tool call 的脚本，让循环在第 max_tool_calls 次后停止
    many_scripts = []
    for _ in range(5):
        many_scripts.append(make_tool_response("调用", [ToolCall(id="c1", name="get_weather", arguments='{"city":"上海"}')]))
    fake8 = FakeClient(many_scripts)
    wrapper8 = OpenAIModelWrapper(fake8, tool_registry=reg, config={"auto_execute_tools": True, "max_tool_calls": 2})
    r8 = wrapper8.chat([{"role": "user", "content": "hi"}])
    check("T19", len(r8.tool_calls) == 2 and len(fake8.calls) == 2,
          f"tool_calls={len(r8.tool_calls)}, calls={len(fake8.calls)}")

    # ===== T20: Embed =====
    print("=== T20: Embed ===")

    fake9 = FakeClient([])
    wrapper9 = OpenAIModelWrapper(fake9, config={})
    embeds = wrapper9.embed(["文本1", "文本2"])
    check("T20a", isinstance(embeds[0], EmbedResult))
    check("T20b", embeds[0].embedding == [0.1, 0.2, 0.3])
    check("T20c", embeds[0].model == "embed-model")

    # ===== T21-T22: db user_profiles =====
    print("=== T21-T22: db user_profiles ===")

    import shutil
    import tempfile
    from local_db.db import LocalDB

    tmpdir = tempfile.mkdtemp()
    db = LocalDB(db_path=_os.path.join(tmpdir, "test.db"))

    tables = db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {r[0] for r in tables}
    check("T21", "user_profiles" in table_names, f"缺少 user_profiles: {table_names}")

    # T22: save/get UPSERT
    db.save_user_profile("u1", '{"lang": "zh", "topics": ["ai"]}')
    p1 = db.get_user_profile("u1")
    check("T22a", p1 == '{"lang": "zh", "topics": ["ai"]}', f"p1={p1}")

    db.save_user_profile("u1", '{"lang": "en"}')
    p2 = db.get_user_profile("u1")
    count = db._conn.execute("SELECT COUNT(*) FROM user_profiles WHERE user_id='u1'").fetchone()[0]
    check("T22b", p2 == '{"lang": "en"}', f"p2={p2}")
    check("T22c", count == 1, f"应 UPSERT 为 1 条，实际 {count}")

    db.close()
    shutil.rmtree(tmpdir, ignore_errors=True)

    # ===== 汇总 =====
    print(f"\n{'='*40}")
    print(f"总计: {passed} PASS, {failed} FAIL")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        _sys.exit(1)
        