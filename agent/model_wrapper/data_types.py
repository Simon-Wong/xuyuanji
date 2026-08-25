"""模型封装核心数据类型。"""
from __future__ import annotations

from dataclasses import dataclass, field


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
    """流式 chat 的单个 chunk。

    不同场景填充不同字段（互斥，见下表）:
        - 流式文本:            delta_content 有值
        - 流式累积 tool_call:  delta_tool_calls 有值
        - 需用户授权:          tool_calls 有完整列表 + done=True
        - 自动执行(调用):      tool_name + tool_calls
        - 自动执行(结果):      tool_name + tool_result
        - 最终结束:            done=True（可选 usage）
    """
    delta_content: str | None = None        # 增量文本
    delta_tool_calls: list[ToolCall] | None = None  # 增量函数调用（流式累积中）
    tool_calls: list[ToolCall] | None = None        # 完整函数调用（流结束时）
    tool_result: str | None = None                  # 函数执行结果（自动执行模式下）
    tool_name: str | None = None                    # 正在执行的函数名（自动执行模式下）
    usage: dict[str, int] | None = None             # token 统计（仅最终 chunk）
    done: bool = False                               # 是否为最终 chunk


@dataclass
class EmbedResult:
    """embed 返回。"""
    embedding: list[float]
    model: str
    usage: dict[str, int] = field(default_factory=dict)
