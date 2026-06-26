"""Provider & Model 配置系统。

支持用户通过 sunshine.json 配置多个 provider 和 model。
加载顺序：sunshine.json（项目） > ~/.sunshine/config.json > 环境变量 > 内置兜底
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelConfig:
    """单个模型的配置。"""
    name: str                           # 显示名称
    cost: str = "medium"                # very-low | low | medium | high | very-high
    capability: str = "medium"          # very-low | low | medium | high | very-high
    context: int = 128000               # 上下文窗口
    output: int = 16384                 # 最大输出 tokens
    supports_images: bool = False
    supports_tools: bool = True
    tags: list[str] = field(default_factory=list)  # code_generation, planning, search, ...


@dataclass
class ProviderConfig:
    """单个 provider 的配置。"""
    name: str                           # 显示名称
    base_url: str = ""                  # API 地址（空=默认）
    api_key: str = ""                  # API key（支持 {env:VAR}）
    models: dict[str, ModelConfig] = field(default_factory=dict)


# ── Cost / Capability 分级定义 ────────────────────────────────────────

COST_LEVELS = {
    "very-low": 0,   # 免费 / 极低
    "low": 1,        # < $1/M
    "medium": 2,     # $1-5/M
    "high": 3,       # $5-15/M
    "very-high": 4,  # > $15/M
}

CAPABILITY_LEVELS = {
    "very-low": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "very-high": 4,
}


def cost_level_ok(worker_cost: str, max_cost: str) -> bool:
    """检查 worker 的 cost 是否 ≤ max_cost。"""
    return COST_LEVELS.get(worker_cost, 2) <= COST_LEVELS.get(max_cost, 2)


# ── 内置默认模型（OpenCode 常用模型）─────────────────────────────────

DEFAULT_PROVIDERS: dict[str, ProviderConfig] = {
    "anthropic": ProviderConfig(
        name="Anthropic",
        base_url="",
        api_key="{env:ANTHROPIC_API_KEY}",
        models={
            "claude-opus-4-6": ModelConfig(
                name="Claude Opus 4.6",
                cost="very-high", capability="very-high",
                context=200000, output=32768,
                supports_images=True,
                tags=["planning", "architecture", "reasoning", "code_generation", "review"],
            ),
            "claude-sonnet-4-6": ModelConfig(
                name="Claude Sonnet 4.6",
                cost="high", capability="high",
                context=200000, output=16384,
                supports_images=True,
                tags=["code_generation", "reasoning", "planning", "review", "general"],
            ),
            "claude-haiku-4-5": ModelConfig(
                name="Claude Haiku 4.5",
                cost="low", capability="medium",
                context=200000, output=8192,
                tags=["search", "code_generation", "document", "test"],
            ),
        },
    ),
    "openai": ProviderConfig(
        name="OpenAI",
        base_url="",
        api_key="{env:OPENAI_API_KEY}",
        models={
            "gpt-5": ModelConfig(
                name="GPT-5",
                cost="very-high", capability="very-high",
                context=128000, output=16384,
                supports_images=True,
                tags=["planning", "code_generation", "reasoning", "search", "review"],
            ),
            "gpt-5-mini": ModelConfig(
                name="GPT-5 Mini",
                cost="medium", capability="medium",
                context=128000, output=16384,
                tags=["code_generation", "search", "general"],
            ),
            "gpt-5-nano": ModelConfig(
                name="GPT-5 Nano",
                cost="low", capability="low",
                context=128000, output=16384,
                tags=["search", "document", "test"],
            ),
        },
    ),
    "deepseek": ProviderConfig(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key="{env:DEEPSEEK_API_KEY}",
        models={
            "deepseek-v4-pro": ModelConfig(
                name="DeepSeek V4 Pro",
                cost="high", capability="high",
                context=128000, output=16384,
                tags=["code_generation", "reasoning", "planning", "general"],
            ),
            "deepseek-v4-flash": ModelConfig(
                name="DeepSeek V4 Flash",
                cost="low", capability="medium",
                context=128000, output=8192,
                tags=["code_generation", "search", "test"],
            ),
        },
    ),
    "qwen": ProviderConfig(
        name="Qwen (阿里)",
        base_url="",
        api_key="{env:QWEN_API_KEY}",
        models={
            "qwen3-8b": ModelConfig(
                name="Qwen3 8B",
                cost="very-low", capability="low",
                context=32768, output=8192,
                tags=["search", "document", "test"],
            ),
            "qwen3-72b": ModelConfig(
                name="Qwen3 72B",
                cost="low", capability="high",
                context=32768, output=8192,
                tags=["code_generation", "reasoning", "search", "general"],
            ),
        },
    ),
    "ollama": ProviderConfig(
        name="Ollama (本地)",
        base_url="http://localhost:11434/v1",
        api_key="",
        models={},
    ),
}


# ── sunshine.json 加载 ───────────────────────────────────────────────


def _resolve_env(value: str) -> str:
    """解析 {env:VAR} 占位符。"""
    import os

    if value.startswith("{env:") and value.endswith("}"):
        var = value[5:-1]
        return os.environ.get(var, "")
    return value


def load_sunshine_config(workspace: str) -> dict[str, ProviderConfig]:
    """加载并合并所有来源的 provider 配置。

    优先级：sunshine.json > ~/.sunshine/config.json > 内置默认
    """
    import os

    providers: dict[str, ProviderConfig] = {
        k: ProviderConfig(
            name=v.name, base_url=v.base_url, api_key=_resolve_env(v.api_key),
            models=dict(v.models),
        )
        for k, v in DEFAULT_PROVIDERS.items()
    }

    # 合并全局配置
    global_path = Path.home() / ".sunshine" / "config.json"
    if global_path.exists():
        _merge_config_file(providers, global_path)

    # 合并项目配置（最高优先级）
    project_path = Path(workspace) / "sunshine.json"
    if project_path.exists():
        _merge_config_file(providers, project_path)

    # 如果 .env 有默认模型配置
    env_model = os.environ.get("SUNSHINE_MODEL", "")
    env_provider = os.environ.get("SUNSHINE_PROVIDER", "")
    env_key = os.environ.get("SUNSHINE_API_KEY", "")
    env_url = os.environ.get("SUNSHINE_BASE_URL", "")
    if env_model and env_provider:
        if env_provider not in providers:
            providers[env_provider] = ProviderConfig(name=env_provider)
        providers[env_provider].models[env_model] = ModelConfig(
            name=env_model, cost="medium", capability="medium",
        )
    if env_key and env_provider and env_provider in providers:
        providers[env_provider].api_key = env_key
    if env_url and env_provider and env_provider in providers:
        providers[env_provider].base_url = env_url

    return providers


def _merge_config_file(providers: dict[str, ProviderConfig], path: Path):
    """将 config.json 合并到 providers。

    支持两种格式：
      新版：{"providers": {"openai": {"base_url": "...", "api_key": "..."}}}
      旧版：{"openai_api_key": "sk-xxx", "openai_base_url": "..."}
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return

    # 新版格式：嵌套 providers
    cfg_providers = data.get("providers", data.get("provider", {}))
    if isinstance(cfg_providers, dict):
        for prov_id, prov_data in cfg_providers.items():
            if prov_id not in providers:
                providers[prov_id] = ProviderConfig(name=prov_data.get("name", prov_id))
            p = providers[prov_id]
            if "base_url" in prov_data:
                p.base_url = prov_data["base_url"]
            if "api_key" in prov_data:
                p.api_key = _resolve_env(prov_data["api_key"])
            models = prov_data.get("models", {})
            if isinstance(models, dict):
                for model_id, model_data in models.items():
                    p.models[model_id] = ModelConfig(
                        name=model_data.get("name", model_id),
                        cost=model_data.get("cost", "medium"),
                        capability=model_data.get("capability", "medium"),
                        context=model_data.get("context", 128000),
                        output=model_data.get("output", 16384),
                        supports_images=model_data.get("supports_images", False),
                        supports_tools=model_data.get("supports_tools", True),
                        tags=model_data.get("tags", []),
                    )

    # 旧版格式：扁平 key → 映射到 provider
    provider_key_map = {
        "openai_api_key": ("openai", "api_key"),
        "openai_base_url": ("openai", "base_url"),
        "anthropic_api_key": ("anthropic", "api_key"),
        "anthropic_base_url": ("anthropic", "base_url"),
    }
    for flat_key, (prov_id, attr) in provider_key_map.items():
        value = data.get(flat_key, "")
        if value:
            if prov_id not in providers:
                providers[prov_id] = ProviderConfig(name=prov_id)
            setattr(providers[prov_id], attr, value)


def save_sunshine_config(workspace: str, providers: dict[str, ProviderConfig]):
    """保存完整的 provider 配置到 sunshine.json。"""
    path = Path(workspace) / "sunshine.json"
    data: dict = {"providers": {}}
    for pid, p in providers.items():
        data["providers"][pid] = {
            "name": p.name,
            "base_url": p.base_url,
            "api_key": p.api_key,
            "models": {
                mid: {
                    "name": m.name,
                    "cost": m.cost,
                    "capability": m.capability,
                    "context": m.context,
                    "output": m.output,
                    "tags": m.tags,
                }
                for mid, m in p.models.items()
            },
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
