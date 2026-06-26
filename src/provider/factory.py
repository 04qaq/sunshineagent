"""ProviderFactory for creating LLM clients.

从 ProviderRegistry 动态获取 API key 和 base_url。
"""

from src.provider.anthropic_client import AnthropicClient
from src.provider.base import ProviderClient
from src.provider.openai_client import OpenAIClient


class ProviderFactory:
    def __init__(self, registry=None):
        self._registry = registry
        self._clients: dict[str, ProviderClient] = {}

    def set_registry(self, registry):
        self._registry = registry
        self._clients.clear()

    def create(self, provider_id: str) -> ProviderClient:
        if provider_id in self._clients:
            return self._clients[provider_id]

        key = ""
        url = ""
        if self._registry:
            p = self._registry.get_provider(provider_id)
            if p:
                key = p.api_key
                url = p.base_url

        if provider_id == "anthropic":
            client = AnthropicClient(api_key=key or None, base_url=url or None)
        elif provider_id == "openai":
            client = OpenAIClient(api_key=key or None, base_url=url or None)
        else:
            client = OpenAIClient(api_key=key or None, base_url=url or None)

        self._clients[provider_id] = client
        return client
