"""ModelCatalog — 动态模型目录，支持多 provider。

融合内置默认 + sunshine.json + 环境变量。
"""

from dataclasses import dataclass, field

from src.config.provider import (
    CAPABILITY_LEVELS,
    COST_LEVELS,
    ProviderConfig,
    load_sunshine_config,
)


@dataclass
class ModelEntry:
    """模型注册项。"""
    model_id: str
    provider_id: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_tools: bool = True
    supports_images: bool = False
    input_price: float = 0
    output_price: float = 0
    tags: list[str] = field(default_factory=list)
    cost_tier: str = "medium"       # very-low/low/medium/high/very-high
    capability_tier: str = "medium"


class ModelCatalog:
    """动态模型目录。

    启动时扫描所有 provider 配置，生成 ModelEntry 列表。
    支持按 provider、tag、cost 过滤。
    """

    def __init__(self, workspace: str = ""):
        self._providers: dict[str, ProviderConfig] = {}
        self._models: list[ModelEntry] = []
        if workspace:
            self.reload(workspace)

    def reload(self, workspace: str):
        """重新加载 provider 配置。"""
        self._providers = load_sunshine_config(workspace)
        self._models = self._build_entries()

    def _build_entries(self) -> list[ModelEntry]:
        entries = []
        for pid, p in self._providers.items():
            for mid, m in p.models.items():
                entries.append(ModelEntry(
                    model_id=f"{pid}/{mid}",
                    provider_id=pid,
                    display_name=m.name,
                    context_window=m.context,
                    max_output_tokens=m.output,
                    supports_tools=m.supports_tools,
                    supports_images=m.supports_images,
                    tags=m.tags or [],
                    cost_tier=m.cost,
                    capability_tier=m.capability,
                ))
        return entries

    @property
    def providers(self) -> dict[str, ProviderConfig]:
        return self._providers

    @property
    def models(self) -> list[ModelEntry]:
        return self._models

    def resolve(self, provider_id: str, model_id: str) -> ModelEntry | None:
        """按 provider + model 查找。model_id 可以是短名或 provider/model。"""
        if "/" in model_id:
            parts = model_id.split("/", 1)
            provider_id = parts[0]
            model_id = parts[1]
        for m in self._models:
            if m.provider_id == provider_id and (
                m.model_id.endswith(f"/{model_id}")
                or m.model_id == model_id
            ):
                return m
        return None

    def resolve_fuzzy(self, model_ref: str) -> ModelEntry | None:
        """模糊匹配模型引用。支持：
        - "provider/model"
        - "model" (匹配唯一 provider)
        - "deepseek-v4-flash" (匹配短名)
        """
        if "/" in model_ref:
            return self.resolve("", model_ref)

        matches = [m for m in self._models if model_ref in m.model_id]
        if len(matches) == 1:
            return matches[0]
        matches = [
            m for m in self._models
            if model_ref.lower() in m.model_id.lower()
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def list_by_provider(self, provider_id: str) -> list[ModelEntry]:
        return [m for m in self._models if m.provider_id == provider_id]

    def list_by_tag(self, tag: str) -> list[ModelEntry]:
        return [m for m in self._models if tag in m.tags]

    def list_by_cost(self, max_cost: str) -> list[ModelEntry]:
        """列出 cost ≤ max_cost 的模型。"""
        max_level = COST_LEVELS.get(max_cost, 2)
        return [
            m for m in self._models
            if COST_LEVELS.get(m.cost_tier, 2) <= max_level
        ]

    def list_by_capability(self, min_cap: str) -> list[ModelEntry]:
        """列出 capability ≥ min_cap 的模型。"""
        min_level = CAPABILITY_LEVELS.get(min_cap, 0)
        return [
            m for m in self._models
            if CAPABILITY_LEVELS.get(m.capability_tier, 0) >= min_level
        ]

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        return self._providers.get(provider_id)

    def get_provider_key(self, provider_id: str) -> str:
        """获取 provider 的 API key（已解析环境变量）。"""
        p = self._providers.get(provider_id)
        return p.api_key if p else ""

    def get_provider_url(self, provider_id: str) -> str:
        """获取 provider 的 base_url。"""
        p = self._providers.get(provider_id)
        return p.base_url if p else ""
