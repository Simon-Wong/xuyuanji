"""OpenAI 客户端封装：管理多 provider 连接。"""
from __future__ import annotations

import os
from typing import Any


class OpenAIClient:
    """OpenAI 客户端封装，按 provider 管理连接。

    所有 provider（商用/本地）都走 OpenAI 兼容 API，
    因此统一用 openai.OpenAI 实例，仅 base_url 和 api_key 不同。
    """

    def __init__(self, providers: dict[str, dict], models: dict[str, dict]) -> None:
        self._providers = providers
        self._models = models
        self._clients: dict[str, Any] = {}               # provider_name -> OpenAI 实例
        self._resolved: dict[str, tuple[Any, str]] = {}  # model_key -> (client, model_name)

    def _get_provider_client(self, provider_name: str) -> Any:
        """按 provider 名获取（或懒创建）OpenAI 实例。"""
        if provider_name in self._clients:
            return self._clients[provider_name]

        if provider_name not in self._providers:
            raise KeyError(f"[model_wrapper] 未配置的 provider: {provider_name}")

        from openai import OpenAI

        provider = self._providers[provider_name]
        base_url = provider.get("base_url")
        api_key_env = provider.get("api_key_env")
        # 本地模型（Ollama/vLLM）无 api_key_env，用占位 key
        api_key = os.environ.get(api_key_env) if api_key_env else "EMPTY"

        client = OpenAI(base_url=base_url, api_key=api_key)
        self._clients[provider_name] = client
        return client

    def get_client(self, model_key: str) -> tuple[Any, str]:
        """按 model_key 获取对应的 client 和实际 model 名。

        Returns:
            (client, model_name)
        """
        if model_key in self._resolved:
            return self._resolved[model_key]

        if model_key not in self._models:
            raise KeyError(f"[model_wrapper] 未配置的模型: {model_key}")

        model_cfg = self._models[model_key]
        provider_name = model_cfg["provider"]
        resolved = (self._get_provider_client(provider_name), model_cfg["model"])
        self._resolved[model_key] = resolved
        return resolved

    def chat(
        self,
        model_key: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        """调用 chat.completions.create。"""
        client, model = self.get_client(model_key)
        return client.chat.completions.create(model=model, messages=messages, **kwargs)

    def embed(self, model_key: str, texts: list[str]) -> Any:
        """调用 embeddings.create。"""
        client, model = self.get_client(model_key)
        return client.embeddings.create(model=model, input=texts)
