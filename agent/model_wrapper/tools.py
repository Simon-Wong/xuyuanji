"""函数工具：复用 openai-agents 的 function_tool + 本地 ToolRegistry。

对标 LangChain 的 @tool 装饰器，openai-agents 已提供 function_tool：
    - 用 griffe 解析 docstring（Google/Numpy/Sphinx 自动检测）
    - 从函数签名 + 类型注解动态建 Pydantic model 生成 JSON schema
    - 内置执行入口（同步函数走 asyncio.to_thread，异步函数直接 await）

因此这里不自己造 @tool，只做：
    1. `tool` 直接 re-export openai-agents 的 function_tool
    2. ToolRegistry 适配 FunctionTool（生成 OpenAI schema + 同步执行）
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from agents import function_tool
from agents.tool_context import ToolContext

from agent.model_wrapper.data_types import ToolCall

# 直接复用 openai-agents 的 function_tool（对标 langchain 的 @tool）
tool = function_tool


class ToolRegistry:
    """本地内置函数注册表。

    注册用 @tool（openai-agents function_tool）装饰生成的 FunctionTool，
    生成 OpenAI function calling schema，并执行模型请求的函数调用。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}  # name -> FunctionTool

    def register(self, tool_obj: Any) -> str:
        """注册一个 FunctionTool，返回 tool_id（即 name）。

        tool_obj 必须是由 @tool 装饰生成的 FunctionTool（带 .name 属性）。
        """
        name = getattr(tool_obj, "name", None)
        if not name:
            raise ValueError("register 需要传入由 @tool 装饰生成的 FunctionTool")
        self._tools[name] = tool_obj
        return name

    def get_schemas(self) -> list[dict]:
        """生成所有已注册工具的 function schema（传给 LLM 的 tools 参数）。"""
        return [_to_openai_tool(t) for t in self._tools.values()]

    def execute(self, tool_call: ToolCall) -> str:
        """执行模型请求的函数调用，返回结果字符串。

        函数执行失败时返回错误信息字符串（不抛异常），
        错误串以 '{"error"' 开头，便于上层重试逻辑识别。
        """
        tool_obj = self._tools.get(tool_call.name)
        if tool_obj is None:
            return json.dumps({"error": f"未注册的工具: {tool_call.name}"}, ensure_ascii=False)

        try:
            ctx = ToolContext(
                context=None,
                tool_name=tool_obj.name,
                tool_call_id=tool_call.id,
                tool_arguments=tool_call.arguments,
            )
            result = asyncio.run(tool_obj.on_invoke_tool(ctx, tool_call.arguments))
        except Exception as exc:
            return json.dumps({"error": f"执行失败: {exc}"}, ensure_ascii=False)

        return _to_str(result)

    def remove(self, name: str) -> None:
        """按名称移除已注册的工具。"""
        self._tools.pop(name, None)

    def list_tools(self) -> list[str]:
        """列出所有已注册工具的名称。"""
        return list(self._tools.keys())


def _to_openai_tool(t: Any) -> dict[str, Any]:
    """FunctionTool -> OpenAI chat completion 的 function tool 参数。"""
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description or "",
            "parameters": t.params_json_schema,
            "strict": t.strict_json_schema,
        },
    }


def _to_str(result: Any) -> str:
    """工具返回值统一转字符串。"""
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False)
    return str(result)
