"""ModelCatalog: static model registry with capability annotations."""

from dataclasses import dataclass, field


@dataclass
class ModelEntry:
    model_id: str
    provider_id: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_tools: bool = True
    supports_images: bool = False
    supports_streaming: bool = True
    input_price: float = 0
    output_price: float = 0
    cache_read_price: float = 0
    tags: list[str] = field(default_factory=list)
    reasoning_effort: str | None = None


class ModelCatalog:
    MODELS: list[ModelEntry] = [
        ModelEntry(
            model_id="claude-sonnet-4-6",
            provider_id="anthropic",
            display_name="Claude Sonnet 4.6",
            context_window=200000,
            max_output_tokens=16384,
            supports_images=True,
            input_price=3.0,
            output_price=15.0,
            cache_read_price=0.30,
            tags=["code_generation", "reasoning", "planning", "general"],
        ),
        ModelEntry(
            model_id="claude-opus-4-6",
            provider_id="anthropic",
            display_name="Claude Opus 4.6",
            context_window=200000,
            max_output_tokens=32768,
            supports_images=True,
            input_price=15.0,
            output_price=75.0,
            cache_read_price=1.50,
            tags=["planning", "architecture", "reasoning", "code_generation"],
        ),
        ModelEntry(
            model_id="gpt-5",
            provider_id="openai",
            display_name="GPT-5",
            context_window=128000,
            max_output_tokens=16384,
            supports_images=True,
            input_price=5.0,
            output_price=20.0,
            tags=["planning", "code_generation", "reasoning", "search"],
        ),
        ModelEntry(
            model_id="qwen3-8b",
            provider_id="openai",
            display_name="Qwen3 8B",
            context_window=32768,
            max_output_tokens=8192,
            input_price=0.0,
            output_price=0.0,
            tags=["code_generation", "search", "test"],
        ),
    ]

    def resolve(self, provider_id: str, model_id: str) -> ModelEntry | None:
        for m in self.MODELS:
            if m.provider_id == provider_id and m.model_id == model_id:
                return m
        return None

    def list_by_tag(self, tag: str) -> list[ModelEntry]:
        return [m for m in self.MODELS if tag in m.tags]

    def list_by_provider(self, provider_id: str) -> list[ModelEntry]:
        return [m for m in self.MODELS if m.provider_id == provider_id]
