"""ProviderFactory for creating LLM clients.

从 ModelCatalog 动态获取 API key 和 base_url。
"""

from src.provider.anthropic_client import AnthropicClient
from src.provider.base import ProviderClient
from src.provider.catalog import ModelCatalog
from src.provider.openai_client import OpenAIClient


class ProviderFactory:
    def __init__(self, catalog: ModelCatalog | None = None):
        self._catalog = catalog
        self._clients: dict[str, ProviderClient] = {}

    def set_catalog(self, catalog: ModelCatalog):
        self._catalog = catalog
        self._clients.clear()

    def create(self, provider_id: str) -> ProviderClient:
        if provider_id in self._clients:
            return self._clients[provider_id]

        key = ""
        url = ""
        if self._catalog:
            key = self._catalog.get_provider_key(provider_id)
            url = self._catalog.get_provider_url(provider_id)

        if provider_id == "anthropic":
            client = AnthropicClient(api_key=key or None, base_url=url or None)
        elif provider_id == "openai":
            client = OpenAIClient(api_key=key or None, base_url=url or None)
        else:
            # 未知 provider，用 OpenAI 兼容模式
            client = OpenAIClient(api_key=key or None, base_url=url or None)

        self._clients[provider_id] = client
        return client
