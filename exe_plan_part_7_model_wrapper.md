# 开发任务 7 执行计划：模型封装 (Model Wrapper)
> 对应 `dev_plan.md` 阶段 1 的任务 #7「模型封装」
> 本文档为任务 #7 的讨论结论定版与执行计划。

---

## 一、文档定位与范围

### 1.1 任务目标
实现模型封装，统一封装商用模型和本地模型的调用细节，支持流式输出、函数调用、嵌入向量，提供统一接口供路由器/任务组使用。

### 1.2 支持的模型来源

| 来源 | 提供方 | API 协议 |
|---|---|---|
| 商用模型 | DashScope (通义千问) / OpenAI / 其他 | OpenAI 兼容 API |
| 本地模型 | Ollama | OpenAI 兼容 API (`http://localhost:11434/v1`) |
| 本地模型 | vLLM | OpenAI 兼容 API (`http://localhost:8000/v1`) |

所有来源都使用 OpenAI 兼容 API，因此用 `openai` Python 库作为统一客户端。

### 1.3 与其他模块的关系
- **上游**：路由器（简单路径）/ 任务组（复杂路径）调用模型封装
- **下游**：LLM API（通过 openai 库）、能力管理器（读取可用工具 schema）
- **依赖**：`agent/observability`（日志）、`config`（配置）、`local_db`（用户画像表）
- **第三方依赖**：`openai`（LLM API 客户端）、`pydantic`（@tool 装饰器 schema）

### 1.4 核心约束
- **第三方模块封装**：openai 库做好封装，便于切换实现
- **对标 LangChain @tool**：parse_docstring=True，支持 Pydantic BaseModel
- **对标 deepseek-harness**：流式 chunk 协议、tool-call/tool-result 模式

---

## 二、已讨论并确认的设计决策

### 2.1 API 客户端

使用 `openai` Python 库（方案 A）。
- 官方维护，支持 streaming、function calling、自动重试
- 所有 provider（商用/本地）都走 OpenAI 兼容 API
- 做好封装，便于后续切换

### 2.2 配置结构

providers + models 分离：

```json
"providers": {
    "Commercial": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY"
    },
    "Ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key_env": null
    },
    "vLLM": {
        "base_url": "http://localhost:8000/v1",
        "api_key_env": null
    }
},
"models": {
    "default":     {"provider": "Commercial", "model": "qwen-plus",        "type": "chat",      "max_context_tokens": 32768},
    "planner":     {"provider": "Commercial", "model": "qwen-plus",        "type": "chat",      "max_context_tokens": 32768},
    "selector":    {"provider": "Commercial", "model": "qwen-turbo",       "type": "chat",      "max_context_tokens": 32768},
    "local-chat":  {"provider": "Ollama",     "model": "qwen2.5:7b",       "type": "chat",      "max_context_tokens": 8192},
    "local-embed": {"provider": "Ollama",     "model": "nomic-embed-text", "type": "embedding", "max_context_tokens": 8192}
}
```

- `providers` 定义 API 端点（base_url + api_key_env）
- `models` 定义具体模型，引用 provider 名
- `type` 区分 `chat` 和 `embedding`
- `api_key_env` 为 null 时不需要 API Key（本地模型）

### 2.3 system prompt 拼装

模型封装负责拼装 system prompt，拼装顺序：

```
1. 静态部分（配置读：角色定义 + 约束）
2. 可用资源（动态：内置工具由调用方传入 + 其他工具从能力管理器读取）
```

**不包含用户画像**。用户画像由未来的"演化机"组件独立管理，存入 local_db 新表，不归模型封装的 system prompt 拼装流程。

### 2.4 @tool 装饰器

对标 LangChain 的 `@tool` 装饰器：

- **parse_docstring=True**：默认解析 docstring 中的参数描述（Google 风格 `Args:` 段）
- **支持 Pydantic BaseModel**：可作为 `args_schema` 提供更精确的参数 schema
- **支持基本类型**：str / int / float / bool，自动映射到 JSON Schema 类型
- **自动生成 schema**：从函数名、docstring、类型注解自动生成 OpenAI function calling 格式的 schema

使用示例：

```python
# 基本类型方式
@tool
def get_weather(city: str) -> str:
    """查询指定城市的天气。
    
    Args:
        city: 城市名称，如"上海"
    """
    return f"{city}今天晴朗"

# Pydantic BaseModel 方式
class SearchInput(BaseModel):
    query: str = Field(description="搜索关键词")
    limit: int = Field(default=10, description="最大返回数量")

@tool(args_schema=SearchInput)
def search_database(query: str, limit: int = 10) -> str:
    """搜索客户数据库。"""
    return f"找到 {limit} 条结果"
```

生成的 schema 格式：
```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市的天气。",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称，如\"上海\""}
            },
            "required": ["city"]
        }
    }
}
```

### 2.5 函数调用流程

自动循环模式，配置控制循环次数和重试：

```
1. 调用方注册本地函数到 ToolRegistry
2. 模型封装调 LLM 时，把 ToolRegistry.get_schemas() 作为 tools 参数传入
3. LLM 返回 tool_calls
4. 模型封装调用 ToolRegistry.execute(tool_call) 执行函数
5. 把执行结果作为 tool_result 追加到 messages，再次调 LLM
6. 重复 3-5 直到 LLM 不再返回 tool_calls 或达到 max_tool_calls
7. 返回最终 content
```

配置参数：
```json
"model_wrapper": {
    "auto_execute_tools": true,
    "max_tool_calls": 10,
    "max_retries": 3
}
```

- `auto_execute_tools`：true=自动执行循环，false=需用户授权
- `max_tool_calls`：最大循环次数（防止无限循环）
- `max_retries`：单次函数调用失败时的重试次数

### 2.6 流式 + 函数调用

#### 非流式模式

```
1. 调一次 LLM，等全部返回
2. 如果有 tool_calls，根据 auto_execute 开关：
   ├─ true  → 自动执行 → 追加结果 → 回到步骤1
   └─ false → 返回 tool_calls 给调用方 → 等用户授权后再次调用
```

#### 流式模式

```
1. 开始流式接收
2. 边接收边 yield delta_content → 用户实时看到部分文本
3. 同时累积 delta_tool_calls
4. 流结束后，拿到完整的 tool_calls
5. 根据配置开关：
   ├─ auto_execute=true  → 自动执行函数 → 追加结果 → 回到步骤1（新一轮流式）
   │                       中间过程也 yield（tool_call + tool_result chunk）
   └─ auto_execute=false → yield tool_calls 给调用方 → 停止，等用户授权
```

流式模式下，中间轮次（函数调用过程）也 yield 给调用方，便于展示器展示中间过程。

### 2.7 用户画像表

local_db 新增一张表：

```sql
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id     TEXT PRIMARY KEY,
    profile     TEXT NOT NULL,    -- JSON: 用户画像数据
    updated_at  TEXT NOT NULL
);
```

- 由未来的"演化机"组件写入和更新
- 模型封装不负责总结用户画像
- 模型封装的 system prompt 拼装不包含用户画像

LocalDB 新增方法：
```python
def save_user_profile(self, user_id: str, profile: str) -> None: ...  # UPSERT
def get_user_profile(self, user_id: str) -> str | None: ...
```

---

## 三、核心数据类型

### 3.1 `agent/model_wrapper/types.py`

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ToolCall:
    """模型请求的函数调用。"""
    id: str                    # 调用 ID（模型生成，用于关联结果）
    name: str                  # 函数名
    arguments: str             # JSON 字符串参数

@dataclass
class ChatResult:
    """非流式 chat 返回。"""
    content: str                           # 文本回复
    tool_calls: list[ToolCall] = field(default_factory=list)  # 函数调用请求
    usage: dict[str, int] = field(default_factory=dict)       # token 统计
    role: str = "assistant"

@dataclass
class ChatChunk:
    """流式 chat 的单个 chunk。"""
    delta_content: str | None = None        # 增量文本
    delta_tool_calls: list[ToolCall] | None = None  # 增量函数调用（流式累积中）
    tool_calls: list[ToolCall] | None = None        # 完整函数调用（流结束时）
    tool_result: str | None = None                  # 函数执行结果（自动执行模式下）
    tool_name: str | None = None                    # 正在执行的函数名（自动执行模式下）
    usage: dict[str, int] | None = None             # token 统计（仅最后一个 chunk）
    done: bool = False                               # 是否为最终 chunk

@dataclass
class EmbedResult:
    """embed 返回。"""
    embedding: list[float]
    model: str
    usage: dict[str, int] = field(default_factory=dict)
```

### 3.2 ChatChunk 的不同场景

| 场景 | chunk 内容 |
|---|---|
| 流式文本 | delta_content="xxx", done=False |
| 流式累积 tool_call | delta_tool_calls=[...], done=False |
| 流结束，有 tool_calls（需授权） | tool_calls=[完整列表], done=True |
| 自动执行中：函数调用 | tool_name="get_weather", tool_calls=[...], done=False |
| 自动执行中：函数结果 | tool_name="get_weather", tool_result="上海晴朗", done=False |
| 最终结束 | content已全部yield, done=True, usage={...} |

---

## 四、统一接口设计

### 4.1 ModelWrapper 接口

```python
class ModelWrapper(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],    # 专用上下文（不含 system prompt）
        *,
        system_prompt_static: str | None = None,  # 静态部分（配置读）
        system_prompt_dynamic: str | None = None,  # 动态部分（调用方传）
        tools: list[dict] | None = None,    # function calling schema 列表
        stream: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        trace_id: str | None = None,
    ) -> ChatResult | Iterator[ChatChunk]:
        """对话接口。
        
        system_prompt 拼装：模型封装内部把 static + dynamic 拼接，
        先静态后动态，拼好后插入 messages 开头。
        
        stream=True 时返回 Iterator[ChatChunk]，否则返回 ChatResult。
        """
        ...

    def embed(
        self,
        texts: list[str],
        *,
        trace_id: str | None = None,
    ) -> list[EmbedResult]:
        """嵌入向量接口。"""
        ...
```

### 4.2 ToolRegistry 接口

```python
class ToolRegistry:
    """本地内置函数注册表。"""

    def register(self, tool_func: Callable) -> str:
        """注册一个 @tool 装饰过的函数，返回 tool_id。
        
        tool_func 必须已被 @tool 装饰，带有 _tool_schema 属性。
        """
        ...

    def get_schemas(self) -> list[dict]:
        """生成所有已注册工具的 function schema（传给 LLM 的 tools 参数）。"""
        ...

    def execute(self, tool_call: ToolCall) -> str:
        """执行模型请求的函数调用，返回结果字符串。"""
        ...

    def remove(self, name: str) -> None:
        """按名称移除已注册的工具。"""
        ...

    def list_tools(self) -> list[str]:
        """列出所有已注册工具的名称。"""
        ...
```

### 4.3 @tool 装饰器

```python
def tool(
    func: Callable | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    args_schema: type[BaseModel] | None = None,
) -> Callable:
    """装饰器：把普通函数注册为 LLM 可调用的工具。
    
    自动从类型注解生成 JSON Schema，从 docstring 生成描述。
    parse_docstring=True：解析 Google 风格 docstring 的 Args 段。
    
    用法:
        @tool
        def get_weather(city: str) -> str:
            \"\"\"查询天气。
            
            Args:
                city: 城市名
            \"\"\"
            return f"{city}晴朗"
        
        @tool(args_schema=SearchInput)
        def search(query: str, limit: int = 10) -> str:
            \"\"\"搜索数据库。\"\"\"
            return "results"
    """
    ...
```

---

## 五、实现范围与文件结构

### 5.1 目录结构

```
agent/
├── model_wrapper/
│   ├── __init__.py           # re-export: ModelWrapper, ToolRegistry, tool, ChatResult, ChatChunk, ToolCall, EmbedResult
│   ├── wrapper.py            # ModelWrapper 接口 + OpenAIModelWrapper 实现
│   ├── client.py             # OpenAI 客户端封装（provider 连接管理）
│   ├── tools.py              # ToolRegistry + @tool 装饰器
│   └── types.py              # ChatResult / ChatChunk / ToolCall / EmbedResult
```

### 5.2 `agent/model_wrapper/types.py`

数据类定义（见三、核心数据类型）。

### 5.3 `agent/model_wrapper/tools.py`

- `@tool` 装饰器实现
  - Python 类型 → JSON Schema 类型映射：str→string, int→integer, float→number, bool→boolean
  - Google 风格 docstring 解析（`Args:` 段提取参数描述）
  - Pydantic BaseModel 支持（从 model_json_schema() 生成）
  - 装饰后的函数附加 `_tool_schema` 属性
- `ToolRegistry` 类
  - register / get_schemas / execute / remove / list_tools
  - execute 内部：解析 arguments JSON → 调用函数 → 返回结果字符串
  - 异常处理：函数执行失败时返回错误信息字符串

### 5.4 `agent/model_wrapper/client.py`

```python
class OpenAIClient:
    """OpenAI 客户端封装，管理多 provider 连接。"""

    def __init__(self, providers: dict[str, dict], models: dict[str, dict]):
        """初始化，按 provider 创建 OpenAI client 实例。"""
        ...

    def get_client(self, model_key: str) -> tuple[OpenAI, str]:
        """按 model_key 获取对应的 client 和实际 model 名。
        
        返回: (client, model_name)
        """
        ...

    def chat(self, model_key: str, messages: list[dict], **kwargs) -> Any:
        """调用 chat.completions.create。"""
        ...

    def embed(self, model_key: str, texts: list[str]) -> Any:
        """调用 embeddings.create。"""
        ...
```

### 5.5 `agent/model_wrapper/wrapper.py`

```python
class OpenAIModelWrapper:
    """模型封装统一实现。"""

    def __init__(
        self,
        client: OpenAIClient,
        tool_registry: ToolRegistry | None = None,
        config: dict | None = None,
    ): ...

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
        """对话接口。"""
        # 1. 拼装 system prompt
        sp = ""
        if system_prompt_static:
            sp += system_prompt_static
        if system_prompt_dynamic:
            sp += "\n" + system_prompt_dynamic
        if sp:
            messages = [{"role": "system", "content": sp}] + messages

        # 2. 合并 tools（内置 + 调用方传入）
        all_tools = []
        if self._tool_registry:
            all_tools.extend(self._tool_registry.get_schemas())
        if tools:
            all_tools.extend(tools)

        # 3. 调用
        if stream:
            return self._chat_stream(messages, all_tools, temperature, max_tokens, trace_id)
        else:
            return self._chat_sync(messages, all_tools, temperature, max_tokens, trace_id)

    def _chat_sync(self, messages, tools, temperature, max_tokens, trace_id) -> ChatResult:
        """非流式调用，含自动循环。"""
        ...

    def _chat_stream(self, messages, tools, temperature, max_tokens, trace_id) -> Iterator[ChatChunk]:
        """流式调用，含自动循环。"""
        ...

    def embed(self, texts, *, trace_id=None) -> list[EmbedResult]:
        """嵌入向量。"""
        ...

    def _execute_tools(self, tool_calls: list[ToolCall]) -> list[tuple[ToolCall, str]]:
        """执行函数调用列表。"""
        ...
```

### 5.6 `agent/model_wrapper/__init__.py`

```python
from agent.model_wrapper.types import ChatResult, ChatChunk, ToolCall, EmbedResult
from agent.model_wrapper.tools import ToolRegistry, tool
from agent.model_wrapper.wrapper import OpenAIModelWrapper
from agent.model_wrapper.client import OpenAIClient

__all__ = [
    "ChatResult", "ChatChunk", "ToolCall", "EmbedResult",
    "ToolRegistry", "tool",
    "OpenAIModelWrapper", "OpenAIClient",
]
```

---

## 六、配置文件更新

### 6.1 `config/agent.default.json`

将现有 `model` 段替换为 `providers` + `models` + `model_wrapper`：

```json
"providers": {
    "Commercial": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY"
    },
    "Ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key_env": null
    },
    "vLLM": {
        "base_url": "http://localhost:8000/v1",
        "api_key_env": null
    }
},
"models": {
    "default":     {"provider": "Commercial", "model": "qwen-plus",        "type": "chat",      "max_context_tokens": 32768},
    "planner":     {"provider": "Commercial", "model": "qwen-plus",        "type": "chat",      "max_context_tokens": 32768},
    "selector":    {"provider": "Commercial", "model": "qwen-turbo",       "type": "chat",      "max_context_tokens": 32768},
    "local-chat":  {"provider": "Ollama",     "model": "qwen2.5:7b",       "type": "chat",      "max_context_tokens": 8192},
    "local-embed": {"provider": "Ollama",     "model": "nomic-embed-text","type": "embedding", "max_context_tokens": 8192}
},
"model_wrapper": {
    "auto_execute_tools": true,
    "max_tool_calls": 10,
    "max_retries": 3
}
```

### 6.2 `model` 段迁移

现有 `model` 段中的 `max_context_tokens` 移入 `models` 各模型定义中。`token_counter.model` 字段保留不变。

---

## 七、db 新增表

### 7.1 user_profiles 表

```sql
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id     TEXT PRIMARY KEY,
    profile     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

LocalDB 新增方法：
```python
def save_user_profile(self, user_id: str, profile: str) -> None: ...  # UPSERT
def get_user_profile(self, user_id: str) -> str | None: ...
```

---

## 八、验收标准

### 8.1 @tool 装饰器（T1-T5）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T1 | 基本类型 @tool 生成 schema | name/description/parameters 正确 |
| T2 | docstring 参数描述解析 | properties 中每个参数有 description |
| T3 | Pydantic BaseModel @tool 生成 schema | Field(description=...) 正确提取 |
| T4 | 自定义 name 和 description 覆盖 | 装饰器参数优先于函数默认值 |
| T5 | 必填参数标记 required | 无默认值的参数在 required 列表中 |

### 8.2 ToolRegistry（T6-T9）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T6 | register 注册工具 | get_schemas 返回包含该工具 |
| T7 | execute 执行函数调用 | 返回函数执行结果字符串 |
| T8 | execute 参数解析正确 | JSON arguments 正确解析为函数参数 |
| T9 | remove 移除工具 | get_schemas 不再包含该工具 |

### 8.3 OpenAIClient（T10-T11）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T10 | 多 provider 连接管理 | 不同 model_key 返回不同 client |
| T11 | 无 api_key_env 时正常工作 | 本地模型不报错 |

### 8.4 ModelWrapper 非流式（T12-T15）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T12 | 非流式 chat 返回 ChatResult | content/usage 字段存在 |
| T13 | system prompt 拼装正确 | 静态+动态拼接，插入 messages 开头 |
| T14 | 非流式 + 函数调用自动循环 | 自动执行 tool_calls 并重试 LLM |
| T15 | 非流式 + auto_execute=False | 返回 tool_calls 不自动执行 |

### 8.5 ModelWrapper 流式（T16-T19）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T16 | 流式 chat 返回 Iterator[ChatChunk] | 逐个 yield delta_content |
| T17 | 流式 + 函数调用自动循环 | yield tool_call + tool_result chunk |
| T18 | 流式 + auto_execute=False | 最后 yield 完整 tool_calls, done=True |
| T19 | max_tool_calls 限制循环 | 达到上限后停止，返回当前结果 |

### 8.6 Embed（T20）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T20 | embed 返回 EmbedResult | embedding 为 list[float]，维度正确 |

### 8.7 db 新增表（T21-T22）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T21 | user_profiles 表存在 | 建表成功 |
| T22 | save/get_user_profile UPSERT 正确 | 同一 user_id 更新不新增 |

---

## 九、结论：所有设计点已确认

| 讨论项 | 最终结论（已确认） |
|---|---|
| **API 客户端** | `openai` Python 库 |
| **配置 provider 名** | Commercial（商用模型） |
| **配置结构** | providers + models 分离 |
| **system prompt 拼装** | 模型封装负责，顺序：静态 → 可用资源 |
| **用户画像** | 存 local_db user_profiles 表，由未来"演化机"组件管理，不纳入 system prompt |
| **@tool 装饰器** | parse_docstring=True，支持 Pydantic BaseModel，对标 LangChain |
| **函数调用流程** | 自动循环，配置 max_tool_calls + max_retries |
| **auto_execute 开关** | true=自动执行循环，false=需用户授权 |
| **流式 + 函数调用** | 先收集完 tool_calls，再根据开关决定自动执行或等用户授权 |
| **中间轮次可见性** | yield 函数调用过程（tool_call + tool_result chunk） |
| **第三方依赖** | openai（LLM 客户端）、pydantic（@tool schema） |
| **文件结构** | `agent/model_wrapper/`（types.py + tools.py + client.py + wrapper.py + __init__.py） |
| **db 变更** | 新增 user_profiles 表 + 2 个方法 |
| **config 变更** | model 段替换为 providers + models + model_wrapper |
