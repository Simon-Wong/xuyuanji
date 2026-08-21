# 开发任务 6 执行计划：上下文管理器 (Context Manager)
> 对应 `dev_plan.md` 阶段 1 的任务 #6「上下文管理器」
> 本文档为任务 #6 的讨论结论定版与执行计划。

---

## 一、文档定位与范围

### 1.1 任务目标
实现上下文管理器，管理三种上下文概念（原始上下文、压缩上下文、专用上下文），支持对话历史的压缩、提取和持久化，为路由器/模型封装提供可直接使用的消息列表。

### 1.2 三种上下文概念

| 概念 | 含义 | 存储 |
|---|---|---|
| 原始上下文 (Raw Context) | 用户输入原文 + AI 回答原文 + 历史对话原文 | messages 表（已有） |
| 压缩上下文 (Compressed Context) | 对原始上下文的压缩摘要，减少 token 消耗 | compressed_contexts 表（新增） |
| 专用上下文 (Specialized Context) | 从原始/压缩上下文中提取的与当前问题相关的上下文 | specialized_contexts 表（新增） |

关键理解：
- 这 3 个概念都是文本，不是固定结构
- 压缩不是必经步骤——短对话可以 Raw → Specialized 直接跳过压缩
- 专用上下文如果仍然太大，也可以再压缩

### 1.3 与其他模块的关系
- **上游**：Web Server / debug_cli 收到用户消息后调用上下文管理器构建专用上下文
- **下游**：路由器接收专用上下文判断复杂度；模型封装接收消息列表调 LLM
- **依赖**：`local_db`（读写三种上下文）、`agent/observability`（日志）、`config`（配置）
- **LLM 依赖**：通过依赖注入 `llm_call` 回调函数，第一版传 None（只用简单规则），模型封装实现后再注入
- **system prompt**：上下文管理器**不碰** system prompt，输出纯对话消息列表，模型封装负责拼 system prompt 到列表开头

### 1.4 核心约束
- **不引入 LangChain**：按 LangChain 原理自己实现
- **第三方模块封装**：token 计数等第三方能力做好接口封装，便于切换实现
- **过滤噪声暂不实现**：原始上下文不会用到，压缩和专用上下文会有 LLM 处理

---

## 二、已讨论并确认的设计决策

### 2.1 压缩策略

Trim + Summarize 组合：旧消息用 LLM 压缩成摘要，最近消息保留原文。

```
[摘要区] 旧消息 → 用 LLM 压缩成一段摘要（增量更新）
─────────── 阈值线 ───────────
[保留区] 最近 N 条消息 → 保持原文
```

### 2.2 压缩上下文生命周期

增量更新（方案 B）：新消息追加到已有摘要里（"旧摘要 + 新消息 → 新摘要"），不每次重新压缩全部历史。每个对话只维护一份压缩上下文（compressed_contexts 表以 conversation_id 为 UNIQUE）。

### 2.3 专用上下文提取流程

先用简单规则提取，若上下文信息不够，则换 LLM 提取。

```
1. 加载 raw（messages 表）+ compressed（摘要）
2. 简单规则提取：取最近 N 条 + 摘要 → 组装
3. 判断是否"足够"（上下文与当前问题的关联度）
   ├─ 够了 → 返回
   └─ 不够 → 调 LLM（selector 模型）从历史中提取相关内容
4. 保存专用上下文到 specialized_contexts 表
```

"不够"的含义：简单规则取的内容跟当前问题关联度不够，需要 LLM 帮忙从历史里找更相关的内容。

### 2.4 system prompt

- 上下文管理器**不负责**拼 system prompt
- 输出的是纯对话消息列表（不含 system prompt）
- 模型封装自己拼：静态部分（角色定义 + 约束）从配置文件读，动态部分（技能列表等）由调用方传入

理由：
- system prompt 是"指令"不是"上下文"——它告诉模型"你是谁、你能做什么"
- 不同模型可能需要不同的 system prompt 格式
- 上下文管理器只管"历史对话"维度，不管"指令"维度

### 2.5 token 计数

设计 `TokenCounter` 接口，3 个实现可切换：

```python
class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...
    def count_messages(self, messages: list[dict]) -> int: ...
```

| 实现 | 依赖 | 准确性 | 安装大小 |
|---|---|---|---|
| `RoughCounter` | 零（字符数 / 3） | 粗略 | 0 |
| `TiktokenCounter` | tiktoken（~2MB） | OpenAI 精确 / Qwen 近似 | 小 |
| `TokenizerCounter` | tokenizers（~3MB） | 精确（可加载 Qwen tokenizer） | 小 |

配置文件选择使用哪个：

```json
"token_counter": {
    "impl": "rough",
    "model": "qwen-plus"
}
```

第一阶段用 `rough`，等需要精确控制时切换到 `tokenizers`。

### 2.6 压缩阈值配置

3 种方式，配置文件设置开关，同时只 1 种生效：

```json
"context": {
    "compress_threshold": {
        "type": "message_count",      // message_count | token_count | compression_ratio
        "value": 20
        // message_count: 超过 20 条消息时触发压缩
        // token_count: 超过 4000 token 时触发压缩
        // compression_ratio: 上下文大小占模型最大窗口比例超过 0.5 时触发压缩
    },
    "recent_messages": {
        "type": "count",              // count | ratio
        "value": 6
        // count: 保留最近 6 条消息
        // ratio: 保留最近 30% 的消息
    }
}
```

### 2.7 max_context_tokens 获取

`compression_ratio` 类型需要知道模型的最大上下文窗口大小。来源：
- 配置文件中设置
- 或软件启动自检时从 LLM 自身获取（通过 API 查询模型信息）

配置文件新增：

```json
"model": {
    ...
    "max_context_tokens": 32768
}
```

### 2.8 LLM 调用方式

依赖注入：构造时传入 `llm_call` 回调函数。

```python
llm_call: Callable[[list[dict], str], str] | None  # (messages, model_name) -> response_text
```

- 第一版传 None：只用简单规则，不调 LLM 压缩和提取
- 模型封装实现后注入：用于 LLM 压缩和 LLM 提取专用上下文
- 接口可 Mock，便于测试

### 2.9 API 设计

3 个接口，分别对应 3 种上下文：

```python
class ContextManager:
    def __init__(
        self,
        db: LocalDB,
        token_counter: TokenCounter,
        llm_call: Callable[[list[dict], str], str] | None = None,
        config: dict | None = None,
    ): ...

    def get_raw_context(self, conversation_id: str) -> list[dict[str, str]]:
        """获取原始上下文（从 messages 表加载历史消息，按时间升序）。
        
        返回: [{role, content}, ...]
        """

    def get_compressed_context(self, conversation_id: str) -> str | None:
        """获取压缩上下文（从 compressed_contexts 表读取摘要）。
        
        返回: 摘要文本，无则 None。
        """

    def get_specialized_context(
        self,
        conversation_id: str,
        message_id: int,
        trace_id: str,
        user_input: str,
        *,
        compress: bool = True,                              # 是否压缩
        external_raw: list[dict[str, str]] | None = None,   # 外来 raw 数据，None 则从 db 加载
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
```

- `compress=True`：检查是否超阈值，超了就压缩，返回压缩前后两份数据
- `compress=False`：不压缩，`compressed=False`，`pre_compression` 为 None
- `external_raw`：传入则用外来数据作为原始上下文（如任务组有自己的上下文），不传则从 db 加载
- `get_specialized_context` 内部自动调用 `get_raw_context` 和 `get_compressed_context`，调用方只调一个方法

---

## 三、db 新增 2 张表

在 `local_db/db.py` 的 `__init__` 建表脚本中新增：

```sql
CREATE TABLE IF NOT EXISTS compressed_contexts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT    NOT NULL UNIQUE,   -- 每对话一份
    trace_id        TEXT    NOT NULL,
    summary         TEXT    NOT NULL,           -- 压缩摘要文本
    message_count   INTEGER NOT NULL,           -- 压缩时覆盖的消息数
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cc_cid ON compressed_contexts(conversation_id);

CREATE TABLE IF NOT EXISTS specialized_contexts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT    NOT NULL,
    message_id      INTEGER NOT NULL,           -- 对应 messages.id（用户输入）
    trace_id        TEXT    NOT NULL,
    messages        TEXT    NOT NULL,           -- JSON: 消息列表
    retrieval_method TEXT   NOT NULL,           -- rule | llm
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sc_cid ON specialized_contexts(conversation_id);
CREATE INDEX IF NOT EXISTS idx_sc_mid ON specialized_contexts(message_id);
```

LocalDB 新增方法：

```python
# compressed_contexts
def save_compressed_context(self, conversation_id, trace_id, summary, message_count) -> None
    """插入或更新（UPSERT，conversation_id 为 UNIQUE）"""
def get_compressed_context(self, conversation_id) -> str | None
    """读取摘要文本，无则 None"""

# specialized_contexts
def save_specialized_context(self, conversation_id, message_id, trace_id, messages, retrieval_method) -> int
    """保存专用上下文，返回自增 id"""
def get_specialized_context(self, message_id) -> list[dict] | None
    """按 message_id 读取，返回消息列表，无则 None"""
```

---

## 四、实现范围与文件结构

### 4.1 目录结构

```
agent/
├── context/
│   ├── __init__.py          # re-export: ContextManager, TokenCounter
│   ├── manager.py           # ContextManager 类
│   ├── token_counter.py     # TokenCounter 接口 + RoughCounter / TiktokenCounter / TokenizerCounter
│   └── retriever.py         # ContextRetriever 接口 + RuleRetriever / LLMRetriever
```

### 4.2 `agent/context/__init__.py`

```python
from agent.context.manager import ContextManager
from agent.context.token_counter import TokenCounter, RoughCounter

__all__ = ["ContextManager", "TokenCounter", "RoughCounter"]
```

### 4.3 `agent/context/token_counter.py`

```python
"""Token 计数器：接口 + 3 个可切换实现。"""
from __future__ import annotations

from typing import Protocol


class TokenCounter(Protocol):
    """Token 计数器接口，便于切换实现。"""

    def count(self, text: str) -> int:
        """计算文本的 token 数。"""
        ...

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        """计算消息列表的总 token 数。"""
        ...


class RoughCounter:
    """粗略估算：字符数 / 3。零依赖，默认使用。"""

    def count(self, text: str) -> int:
        return max(1, len(text) // 3)

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        return sum(self.count(m.get("content", "")) for m in messages)


class TiktokenCounter:
    """tiktoken 实现：OpenAI 精确，Qwen 近似。"""

    def __init__(self, model: str = "gpt-4") -> None:
        import tiktoken
        self._enc = tiktoken.encoding_for_model(model)

    def count(self, text: str) -> int:
        return len(self._enc.encode(text))

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        return sum(self.count(m.get("content", "")) for m in messages)


class TokenizerCounter:
    """tokenizers 库实现：精确（可加载 Qwen tokenizer）。"""

    def __init__(self, tokenizer_path: str) -> None:
        from tokenizers import Tokenizer
        self._tok = Tokenizer.from_file(tokenizer_path)

    def count(self, text: str) -> int:
        return len(self._tok.encode(text).ids)

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        return sum(self.count(m.get("content", "")) for m in messages)


def create_token_counter(impl: str = "rough", model: str = "") -> TokenCounter:
    """工厂函数：根据配置创建 TokenCounter。"""
    if impl == "tiktoken":
        return TiktokenCounter(model=model)
    elif impl == "tokenizers":
        return TokenizerCounter(tokenizer_path=f"data/{model}_tokenizer.json")
    else:
        return RoughCounter()
```

### 4.4 `agent/context/retriever.py`

```python
"""专用上下文提取器：接口 + 简单规则 + LLM 实现。"""
from __future__ import annotations

from typing import Protocol, Any, Callable


class ContextRetriever(Protocol):
    """专用上下文提取器接口。"""

    def retrieve(
        self,
        raw_messages: list[dict[str, str]],
        compressed_summary: str | None,
        user_input: str,
        recent_count: int = 6,
    ) -> tuple[list[dict[str, str]], str]:
        """提取专用上下文。
        
        返回: (messages, method)  method = "rule" | "llm"
        """
        ...


class RuleRetriever:
    """简单规则提取：取最近 N 条 + 压缩摘要。"""

    def retrieve(self, raw_messages, compressed_summary, user_input, recent_count=6):
        messages = []
        if compressed_summary:
            messages.append({"role": "system", "content": f"对话摘要：{compressed_summary}"})
        messages.extend(raw_messages[-recent_count:])
        return messages, "rule"


class LLMRetriever:
    """LLM 提取：用 LLM 从历史中提取与当前问题相关的内容。"""

    def __init__(self, llm_call: Callable, model: str = "selector"):
        self._llm_call = llm_call
        self._model = model

    def retrieve(self, raw_messages, compressed_summary, user_input, recent_count=6):
        # 用 LLM 从历史中提取相关内容
        prompt = f"从以下对话历史中提取与用户问题相关的内容：\n问题：{user_input}\n历史：{...}"
        extracted = self._llm_call([{"role": "user", "content": prompt}], self._model)
        messages = [{"role": "system", "content": f"相关上下文：{extracted}"}]
        messages.extend(raw_messages[-recent_count:])
        return messages, "llm"
```

### 4.5 `agent/context/manager.py`

```python
"""上下文管理器：管理原始/压缩/专用三种上下文。"""
from __future__ import annotations

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
        db,
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
        """获取原始上下文。"""
        msgs = self._db.get_messages(conversation_id)
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    def get_compressed_context(self, conversation_id: str) -> str | None:
        """获取压缩上下文。"""
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
        """构建专用上下文。"""
        # 1. 加载 raw
        raw = external_raw if external_raw is not None else self.get_raw_context(conversation_id)

        pre_compression = None
        compressed = False

        # 2. 检查是否需要压缩
        if compress and self._should_compress(raw):
            pre_compression = list(raw)
            recent_count = self._get_recent_count(len(raw))
            old_messages = raw[:-recent_count]
            recent_messages = raw[-recent_count:]

            # 3. 压缩旧消息（增量更新）
            existing_summary = self.get_compressed_context(conversation_id)
            if self._llm_call and old_messages:
                new_summary = self._compress_messages(existing_summary, old_messages, trace_id)
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
        import json
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
        self, existing_summary: str | None, old_messages: list[dict], trace_id: str
    ) -> str:
        """用 LLM 增量压缩旧消息。"""
        prompt = f"请将以下对话历史压缩成简洁的摘要：\n"
        if existing_summary:
            prompt += f"已有摘要：{existing_summary}\n"
        prompt += f"新对话：\n"
        for m in old_messages:
            prompt += f"{m['role']}: {m['content']}\n"
        prompt += "请输出压缩后的摘要："

        return self._llm_call([{"role": "user", "content": prompt}], "selector")
```

---

## 五、配置文件更新

### 5.1 `config/agent.default.json` 新增 `context` 段和 `token_counter` 段

```json
"context": {
    "compress_threshold": {
        "type": "message_count",
        "value": 20
    },
    "recent_messages": {
        "type": "count",
        "value": 6
    }
},
"token_counter": {
    "impl": "rough",
    "model": "qwen-plus"
}
```

### 5.2 `config/agent.default.json` 的 `model` 段新增 `max_context_tokens`

```json
"model": {
    "default": "qwen-plus",
    "planner": "qwen-plus",
    "selector": "qwen-turbo",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key_env": "DASHSCOPE_API_KEY",
    "max_context_tokens": 32768
}
```

---

## 六、验收标准

### 6.1 原始上下文（T1-T2）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T1 | get_raw_context 返回 messages 表历史，按时间升序 | role 和 content 正确，顺序正确 |
| T2 | get_raw_context 无历史时返回空列表 | 不报错 |

### 6.2 压缩上下文（T3-T6）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T3 | 压缩阈值 message_count 触发 | 超过配置条数时 compressed=True |
| T4 | 压缩阈值 token_count 触发 | 超过配置 token 数时 compressed=True |
| T5 | 压缩阈值 compression_ratio 触发 | 超过模型窗口比例时 compressed=True |
| T6 | 增量更新：已有摘要 + 新消息 → 新摘要 | compressed_contexts 表 updated_at 更新 |

### 6.3 专用上下文（T7-T10）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T7 | compress=False 时不压缩 | compressed=False, pre_compression=None |
| T8 | compress=True 且未超阈值时不压缩 | compressed=False |
| T9 | compress=True 且超阈值时压缩 | compressed=True, pre_compression 非空 |
| T10 | external_raw 传入时用外来数据 | messages 基于外来数据构建 |

### 6.4 返回值结构（T11-T12）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T11 | 返回 dict 含 messages / compressed / pre_compression | 3 个字段都存在 |
| T12 | messages 是 list[dict] 格式 | 每个元素含 role 和 content |

### 6.5 TokenCounter（T13-T15）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T13 | RoughCounter count 返回正整数 | len(text)//3，最小 1 |
| T14 | RoughCounter count_messages 求和正确 | 各消息 token 数之和 |
| T15 | create_token_counter("rough") 返回 RoughCounter | 类型正确 |

### 6.6 db 新增表（T16-T18）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T16 | compressed_contexts 表存在 | 建表成功 |
| T17 | specialized_contexts 表存在 | 建表成功 |
| T18 | save/get_compressed_context UPSERT 正确 | 同一 conversation_id 更新不新增 |

---

## 七、结论：所有设计点已确认

| 讨论项 | 最终结论（已确认） |
|---|---|
| **上下文概念** | 3 种：原始上下文、压缩上下文、专用上下文 |
| **压缩策略** | Trim + Summarize 组合 |
| **压缩上下文生命周期** | 增量更新（旧摘要 + 新消息 → 新摘要） |
| **专用上下文提取** | 先简单规则，不够再 LLM 提取 |
| **system prompt** | 上下文管理器不碰，模型封装自己拼 |
| **token 计数** | TokenCounter 接口 + 3 个实现（rough / tiktoken / tokenizers），配置切换 |
| **压缩阈值** | 3 种方式（message_count / token_count / compression_ratio），同时只 1 种生效 |
| **保留规则** | 2 种方式（count / ratio），配置切换 |
| **max_context_tokens** | 配置文件设置，或启动自检从 LLM 获取 |
| **LLM 调用** | 依赖注入 llm_call 回调，第一版传 None |
| **过滤噪声** | 暂不实现 |
| **LangChain** | 不引入，按原理自己实现 |
| **db 变更** | 新增 compressed_contexts + specialized_contexts 两张表，records 表保留（存 thought/result/schedule_sheet/artifact） |
| **API** | 3 个接口：get_raw_context / get_compressed_context / get_specialized_context |
| **get_specialized_context 返回** | dict 含 messages / compressed / pre_compression 3 字段 |
| **文件结构** | `agent/context/`（manager.py + token_counter.py + retriever.py + \_\_init\_\_.py） |
