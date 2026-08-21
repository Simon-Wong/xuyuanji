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
