"""ProviderFactory for creating LLM clients."""

from src.config.config import get_config
from src.provider.anthropic_client import AnthropicClient
from src.provider.base import ProviderClient
from src.provider.openai_client import OpenAIClient


class ProviderFactory:
    def __init__(self):
        self._config = get_config()
        self._clients: dict[str, ProviderClient] = {}

    def create(self, provider_id: str) -> ProviderClient:
        if provider_id in self._clients:
            return self._clients[provider_id]

        if provider_id == "anthropic":
            client = AnthropicClient(
                api_key=self._config.anthropic_api_key,
                base_url=self._config.anthropic_base_url,
            )
        elif provider_id == "openai":
            client = OpenAIClient(
                api_key=self._config.openai_api_key,
                base_url=self._config.openai_base_url,
            )
        else:
            raise ValueError(f"Unknown provider: {provider_id}")

        self._clients[provider_id] = client
        return client
