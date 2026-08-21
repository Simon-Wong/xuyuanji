"""上下文管理器：管理原始/压缩/专用三种上下文。"""
from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import json
from typing import Any, Callable

from agent.context.token_counter import TokenCounter, create_token_counter
from agent.context.retriever import RuleRetriever


class ContextManager:
    """上下文管理器。

    三种上下文：
    - 原始上下文：messages 表中的历史消息
    - 压缩上下文：compressed_contexts 表中的摘要
    - 专用上下文：specialized_contexts 表中的提取结果
    """

    def __init__(
        self,
        db: Any,
        token_counter: TokenCounter | None = None,
        llm_call: Callable[[list[dict], str], str] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._db = db
        self._token_counter = token_counter or create_token_counter("rough")
        self._llm_call = llm_call  # None = 第一版只用简单规则
        self._config = config or {}

        # 压缩阈值配置
        ct = self._config.get("compress_threshold", {"type": "message_count", "value": 20})
        self._compress_type = ct.get("type", "message_count")
        self._compress_value = ct.get("value", 20)

        # 保留规则配置
        rm = self._config.get("recent_messages", {"type": "count", "value": 6})
        self._recent_type = rm.get("type", "count")
        self._recent_value = rm.get("value", 6)

    def _get_recent_count(self, total: int) -> int:
        """根据配置计算保留最近几条。"""
        if self._recent_type == "ratio":
            return max(1, int(total * self._recent_value))
        return min(self._recent_value, total)

    def _should_compress(self, messages: list[dict]) -> bool:
        """根据配置判断是否需要压缩。"""
        if self._compress_type == "message_count":
            return len(messages) > self._compress_value
        elif self._compress_type == "token_count":
            return self._token_counter.count_messages(messages) > self._compress_value
        elif self._compress_type == "compression_ratio":
            max_tokens = self._config.get("max_context_tokens", 32768)
            current = self._token_counter.count_messages(messages)
            return current / max_tokens > self._compress_value
        return False

    def get_raw_context(self, conversation_id: str) -> list[dict[str, str]]:
        """获取原始上下文（从 messages 表加载历史消息，按时间升序）。

        返回: [{role, content}, ...]
        """
        msgs = self._db.get_messages(conversation_id)
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    def get_compressed_context(self, conversation_id: str) -> str | None:
        """获取压缩上下文（从 compressed_contexts 表读取摘要）。

        返回: 摘要文本，无则 None。
        """
        return self._db.get_compressed_context(conversation_id)

    def get_specialized_context(
        self,
        conversation_id: str,
        message_id: int,
        trace_id: str,
        user_input: str,
        *,
        compress: bool = True,
        external_raw: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """构建专用上下文。

        内部流程：
        1. 加载 raw（external_raw 或 db messages 表）
        2. 检查是否需要压缩（compress=True 且超阈值时触发）
        3. 压缩：旧消息 LLM 摘要 + 最近 N 条保留
        4. 提取：简单规则（最近 N 条 + 摘要）→ 不够则 LLM 提取
        5. 保存到 db（compressed_contexts / specialized_contexts）

        返回:
            {
                "messages": list[dict],              # 最终消息列表（供模型封装使用）
                "compressed": bool,                  # 是否发生了压缩
                "pre_compression": list[dict] | None # 压缩前的原始消息列表（仅 compressed=True 时）
            }
        """
        # 1. 加载 raw
        raw = external_raw if external_raw is not None else self.get_raw_context(conversation_id)

        pre_compression = None
        compressed = False

        # 2. 检查是否需要压缩
        if compress and self._should_compress(raw):
            pre_compression = list(raw)
            recent_count = self._get_recent_count(len(raw))
            old_messages = raw[:-recent_count] if recent_count < len(raw) else []
            recent_messages = raw[-recent_count:]

            # 3. 压缩旧消息（增量更新）
            existing_summary = self.get_compressed_context(conversation_id)
            if self._llm_call and old_messages:
                new_summary = self._compress_messages(existing_summary, old_messages)
            else:
                # 第一版无 LLM：简单拼接
                new_summary = existing_summary or ""
                for m in old_messages:
                    new_summary += f"\n{m['role']}: {m['content']}"
                new_summary = new_summary[:500]  # 粗截断

            # 保存压缩上下文
            self._db.save_compressed_context(
                conversation_id, trace_id, new_summary, len(old_messages)
            )
            compressed = True

            # 压缩后的 raw = 摘要 + 最近消息
            raw = [{"role": "system", "content": f"对话摘要：{new_summary}"}] + recent_messages

        # 4. 提取专用上下文
        compressed_summary = self.get_compressed_context(conversation_id) if compressed else None
        recent_count = self._get_recent_count(len(raw))
        retriever = RuleRetriever()
        messages, method = retriever.retrieve(raw, compressed_summary, user_input, recent_count)

        # 5. 保存专用上下文
        self._db.save_specialized_context(
            conversation_id, message_id, trace_id,
            json.dumps(messages, ensure_ascii=False), method
        )

        return {
            "messages": messages,
            "compressed": compressed,
            "pre_compression": pre_compression,
        }

    def _compress_messages(
        self, existing_summary: str | None, old_messages: list[dict[str, str]]
    ) -> str:
        """用 LLM 增量压缩旧消息。"""
        prompt = "请将以下对话历史压缩成简洁的摘要：\n"
        if existing_summary:
            prompt += f"已有摘要：{existing_summary}\n"
        prompt += "新对话：\n"
        for m in old_messages:
            prompt += f"{m['role']}: {m['content']}\n"
        prompt += "请输出压缩后的摘要："

        return self._llm_call([{"role": "user", "content": prompt}], "selector")


# ============================================================================
# 测试入口
# ============================================================================
if __name__ == "__main__":
    """验收测试 T1-T18"""
    import os as _os
    import sys as _sys
    import shutil
    import tempfile

    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    if _ROOT not in _sys.path:
        _sys.path.insert(0, _ROOT)

    from local_db.db import LocalDB
    from agent.context.token_counter import RoughCounter, create_token_counter, TokenCounter
    from agent.context.manager import ContextManager

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

    tmpdir = tempfile.mkdtemp()
    db_path = _os.path.join(tmpdir, "test.db")
    db = LocalDB(db_path=db_path)

    # 准备测试数据
    db.save_conversation("u1", "s1", "conv-1", "trace-1")

    # ===== T1-T2: 原始上下文 =====
    print("=== T1-T2: 原始上下文 ===")

    # 先存几条消息
    db.save_message("conv-1", "trace-1", "user", "你好")
    db.save_message("conv-1", "trace-1", "assistant", "你好！有什么可以帮你的？")
    db.save_message("conv-1", "trace-1", "user", "今天天气怎么样")

    cm = ContextManager(db=db, config={"compress_threshold": {"type": "message_count", "value": 20},
                                        "recent_messages": {"type": "count", "value": 6}})

    raw = cm.get_raw_context("conv-1")
    check("T1", len(raw) == 3 and raw[0]["role"] == "user" and raw[0]["content"] == "你好",
          f"raw 不正确: {raw}")

    raw_empty = cm.get_raw_context("conv-nonexistent")
    check("T2", raw_empty == [], f"无历史应返回空列表, 实际: {raw_empty}")

    # ===== T3-T6: 压缩上下文 =====
    print("=== T3-T6: 压缩上下文 ===")

    # T3: message_count 触发
    db3 = LocalDB(db_path=_os.path.join(tmpdir, "test3.db"))
    db3.save_conversation("u1", "s1", "conv-3", "trace-3")
    for i in range(25):
        db3.save_message("conv-3", "trace-3", "user" if i % 2 == 0 else "assistant", f"msg-{i}")
    cm3 = ContextManager(db=db3, config={"compress_threshold": {"type": "message_count", "value": 20},
                                          "recent_messages": {"type": "count", "value": 6}})
    result3 = cm3.get_specialized_context("conv-3", 999, "trace-3", "test")
    check("T3", result3["compressed"] is True, "message_count 阈值未触发压缩")

    # T4: token_count 触发
    db4 = LocalDB(db_path=_os.path.join(tmpdir, "test4.db"))
    db4.save_conversation("u1", "s1", "conv-4", "trace-4")
    for i in range(30):
        db4.save_message("conv-4", "trace-4", "user" if i % 2 == 0 else "assistant",
                         "这是一段较长的消息内容用于测试token计数" * 5)
    cm4 = ContextManager(db=db4, token_counter=RoughCounter(),
                         config={"compress_threshold": {"type": "token_count", "value": 100},
                                 "recent_messages": {"type": "count", "value": 6}})
    result4 = cm4.get_specialized_context("conv-4", 998, "trace-4", "test")
    check("T4", result4["compressed"] is True, "token_count 阈值未触发压缩")

    # T5: compression_ratio 触发
    db5 = LocalDB(db_path=_os.path.join(tmpdir, "test5.db"))
    db5.save_conversation("u1", "s1", "conv-5", "trace-5")
    for i in range(20):
        db5.save_message("conv-5", "trace-5", "user" if i % 2 == 0 else "assistant",
                         "这是一段较长的消息" * 10)
    cm5 = ContextManager(db=db5, token_counter=RoughCounter(),
                         config={"compress_threshold": {"type": "compression_ratio", "value": 0.3},
                                 "max_context_tokens": 500,
                                 "recent_messages": {"type": "count", "value": 6}})
    result5 = cm5.get_specialized_context("conv-5", 997, "trace-5", "test")
    check("T5", result5["compressed"] is True, "compression_ratio 阈值未触发压缩")

    # T6: 增量更新
    # conv-3 已经压缩过一次，再追加消息触发第二次压缩
    import time as _time
    _time.sleep(0.01)
    for i in range(25, 50):
        db3.save_message("conv-3", "trace-3", "user" if i % 2 == 0 else "assistant", f"msg-{i}")
    result6 = cm3.get_specialized_context("conv-3", 996, "trace-3", "test2")
    check("T6", result6["compressed"] is True, "增量压缩未触发")
    summary = db3.get_compressed_context("conv-3")
    check("T6b", summary is not None and len(summary) > 0, "摘要应为非空")

    # ===== T7-T10: 专用上下文 =====
    print("=== T7-T10: 专用上下文 ===")

    # T7: compress=False 时不压缩
    result7 = cm3.get_specialized_context("conv-3", 995, "trace-3", "test", compress=False)
    check("T7", result7["compressed"] is False and result7["pre_compression"] is None,
          f"compress=False 时不应压缩: {result7['compressed']}")

    # T8: compress=True 且未超阈值时不压缩
    db8 = LocalDB(db_path=_os.path.join(tmpdir, "test8.db"))
    db8.save_conversation("u1", "s1", "conv-8", "trace-8")
    db8.save_message("conv-8", "trace-8", "user", "你好")
    db8.save_message("conv-8", "trace-8", "assistant", "你好！")
    cm8 = ContextManager(db=db8, config={"compress_threshold": {"type": "message_count", "value": 20},
                                          "recent_messages": {"type": "count", "value": 6}})
    result8 = cm8.get_specialized_context("conv-8", 994, "trace-8", "test")
    check("T8", result8["compressed"] is False, "未超阈值不应压缩")

    # T9: compress=True 且超阈值时压缩
    result9 = cm3.get_specialized_context("conv-3", 993, "trace-3", "test", compress=True)
    check("T9", result9["compressed"] is True and result9["pre_compression"] is not None,
          "超阈值应压缩且 pre_compression 非空")

    # T10: external_raw 传入时用外来数据
    external = [{"role": "user", "content": "外部消息1"}, {"role": "assistant", "content": "外部回复1"}]
    result10 = cm8.get_specialized_context("conv-8", 992, "trace-8", "test",
                                            compress=False, external_raw=external)
    check("T10", any(m["content"] == "外部消息1" for m in result10["messages"]),
          f"应使用外来数据: {[m['content'] for m in result10['messages']]}")

    # ===== T11-T12: 返回值结构 =====
    print("=== T11-T12: 返回值结构 ===")

    check("T11", all(k in result8 for k in ("messages", "compressed", "pre_compression")),
          f"返回 dict 应含 3 字段: {result8.keys()}")
    check("T12", all("role" in m and "content" in m for m in result8["messages"]),
          "messages 每个元素应含 role 和 content")

    # ===== T13-T15: TokenCounter =====
    print("=== T13-T15: TokenCounter ===")

    rc = RoughCounter()
    check("T13", rc.count("hello world") > 0, "count 应返回正整数")
    check("T14", rc.count_messages([{"content": "abc"}, {"content": "def"}]) == rc.count("abc") + rc.count("def"),
          "count_messages 应为各消息之和")

    tc = create_token_counter("rough")
    check("T15", isinstance(tc, RoughCounter), "create_token_counter('rough') 应返回 RoughCounter")

    # ===== T16-T18: db 新增表 =====
    print("=== T16-T18: db 新增表 ===")

    tables = db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {r[0] for r in tables}
    check("T16", "compressed_contexts" in table_names, f"缺少 compressed_contexts 表: {table_names}")
    check("T17", "specialized_contexts" in table_names, f"缺少 specialized_contexts 表: {table_names}")

    # T18: UPSERT 正确
    db18 = LocalDB(db_path=_os.path.join(tmpdir, "test18.db"))
    db18.save_compressed_context("conv-18", "trace-18", "摘要1", 5)
    db18.save_compressed_context("conv-18", "trace-18", "摘要2", 10)
    rows = db18._conn.execute(
        "SELECT COUNT(*) FROM compressed_contexts WHERE conversation_id='conv-18'"
    ).fetchone()[0]
    summary18 = db18.get_compressed_context("conv-18")
    check("T18", rows == 1 and summary18 == "摘要2",
          f"UPSERT 应只 1 条且为最新值: rows={rows}, summary={summary18}")

    # ===== 清理 =====
    db.close()
    db3.close()
    db4.close()
    db5.close()
    db8.close()
    db18.close()
    shutil.rmtree(tmpdir, ignore_errors=True)

    # ===== 汇总 =====
    print(f"\n{'='*40}")
    print(f"总计: {passed} PASS, {failed} FAIL")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        _sys.exit(1)
