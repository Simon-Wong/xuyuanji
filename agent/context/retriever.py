"""专用上下文提取器：接口 + 简单规则 + LLM 实现。"""
from __future__ import annotations

from typing import Protocol, Callable


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

    def retrieve(
        self,
        raw_messages: list[dict[str, str]],
        compressed_summary: str | None,
        user_input: str,
        recent_count: int = 6,
    ) -> tuple[list[dict[str, str]], str]:
        messages: list[dict[str, str]] = []
        if compressed_summary:
            messages.append({"role": "system", "content": f"对话摘要：{compressed_summary}"})
        messages.extend(raw_messages[-recent_count:])
        return messages, "rule"


class LLMRetriever:
    """LLM 提取：用 LLM 从历史中提取与当前问题相关的内容。"""

    def __init__(self, llm_call: Callable[[list[dict], str], str], model: str = "selector") -> None:
        self._llm_call = llm_call
        self._model = model

    def retrieve(
        self,
        raw_messages: list[dict[str, str]],
        compressed_summary: str | None,
        user_input: str,
        recent_count: int = 6,
    ) -> tuple[list[dict[str, str]], str]:
        # 构建历史文本
        history_text = ""
        if compressed_summary:
            history_text += f"摘要：{compressed_summary}\n"
        for m in raw_messages:
            history_text += f"{m['role']}: {m['content']}\n"

        prompt = (
            f"从以下对话历史中提取与用户问题相关的内容，"
            f"只保留与问题直接相关的信息：\n"
            f"问题：{user_input}\n"
            f"历史：\n{history_text}\n"
            f"请输出相关上下文："
        )
        extracted = self._llm_call([{"role": "user", "content": prompt}], self._model)
        messages: list[dict[str, str]] = [{"role": "system", "content": f"相关上下文：{extracted}"}]
        messages.extend(raw_messages[-recent_count:])
        return messages, "llm"
