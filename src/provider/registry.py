"""ProviderRegistry — 单一模型配置源，对应 OpenCode 的 provider 层。

数据流：
  sunshine.json (项目) > ~/.sunshine/config.json (全局) > DEFAULT_PROVIDERS (内置)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelInfo:
    """模型信息。"""
    provider: str           # "openai"
    model: str              # "gpt-5"
    name: str               # 显示名称 "GPT-5"
    context: int = 128000
    output: int = 16384
    cost: str = "medium"     # very-low/low/medium/high/very-high
    capability: str = "medium"

    @property
    def full_id(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass
class Provider:
    """Provider 配置。"""
    name: str
    protocol: str = "openai-compatible"  # "anthropic" | "openai-compatible"
    api_key: str = ""
    base_url: str = ""
    env_key: str = ""        # 环境变量名，如 "OPENAI_API_KEY"
    models: dict[str, ModelInfo] = field(default_factory=dict)

    def resolve_model(self, model_id: str) -> ModelInfo | None:
        if model_id in self.models:
            return self.models[model_id]
        for m in self.models.values():
            if m.model in model_id or model_id in m.model:
                return m
        return None


# ── 内置默认 Provider ─────────────────────────────────────────────────

DEFAULT_PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider(
        name="Anthropic",
        protocol="anthropic",
        env_key="ANTHROPIC_API_KEY",
        models={
            "claude-opus-4-6": ModelInfo(
                provider="anthropic", model="claude-opus-4-6",
                name="Claude Opus 4.6", context=200000, output=32768,
                cost="very-high", capability="very-high",
            ),
            "claude-sonnet-4-6": ModelInfo(
                provider="anthropic", model="claude-sonnet-4-6",
                name="Claude Sonnet 4.6", context=200000, output=16384,
                cost="high", capability="high",
            ),
            "claude-haiku-4-5": ModelInfo(
                provider="anthropic", model="claude-haiku-4-5",
                name="Claude Haiku 4.5", context=200000, output=8192,
                cost="low", capability="medium",
            ),
        },
    ),
    "openai": Provider(
        name="OpenAI",
        env_key="OPENAI_API_KEY",
        models={
            "gpt-5": ModelInfo(
                provider="openai", model="gpt-5",
                name="GPT-5", cost="very-high", capability="very-high",
            ),
            "gpt-5-mini": ModelInfo(
                provider="openai", model="gpt-5-mini",
                name="GPT-5 Mini", cost="medium", capability="medium",
            ),
        },
    ),
    "deepseek": Provider(
        name="DeepSeek",
        env_key="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        models={
            "deepseek-v4-pro": ModelInfo(
                provider="deepseek", model="deepseek-v4-pro",
                name="DeepSeek V4 Pro", cost="high", capability="high",
            ),
            "deepseek-v4-flash": ModelInfo(
                provider="deepseek", model="deepseek-v4-flash",
                name="DeepSeek V4 Flash", cost="low", capability="medium",
            ),
        },
    ),
    "qwen": Provider(
        name="Qwen",
        env_key="QWEN_API_KEY",
        models={
            "qwen3-8b": ModelInfo(
                provider="qwen", model="qwen3-8b",
                name="Qwen3 8B", context=32768, output=8192,
                cost="very-low", capability="low",
            ),
        },
    ),
}


# ── Registry ────────────────────────────────────────────────────────────


class ProviderRegistry:
    """单一模型配置源。"""

    def __init__(self, workspace: str = ""):
        self._providers: dict[str, Provider] = {}
        self._recent: list[str] = []
        self._default_model: str = "anthropic/claude-sonnet-4-6"
        self._default_provider: str = "anthropic"
        self._workspace = workspace

        self._load_defaults()
        if workspace:
            self.reload(workspace)

    def reload(self, workspace: str):
        """重新加载配置（启动或 /reload 时调用）。"""
        self._workspace = workspace
        self._load_config()
        self._load_project()
        self._autodetect()

    # ── 环境变量读取 ────────────────────────────────────────────────────

    def _read_dotenv(self) -> dict[str, str]:
        """解析 .env 文件，返回 key-value 字典。
        仅当 key 在 os.environ 中不存在时才使用 .env 的值（系统 env var 优先）。
        查找: workspace/.env, ~/.env
        """
        result: dict[str, str] = {}
        paths = []
        if self._workspace:
            paths.append(Path(self._workspace) / ".env")
        paths.append(Path.home() / ".env")
        for path in paths:
            if not path.exists():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        result[k] = v
            except Exception:
                pass
        return result

    def _read_env(self, key: str) -> str:
        """读取环境变量：先查 os.environ，再查 .env 文件。"""
        if not key:
            return ""
        val = os.environ.get(key)
        if val:
            return val
        dotenv = self._read_dotenv()
        return dotenv.get(key, "")

    # ── 自动检测 ────────────────────────────────────────────────────────

    def _autodetect(self):
        """遍历 provider，自动从环境变量发现 API key。
        如果已有 config 中设置的 key 则保留不变。
        """
        for pid, p in self._providers.items():
            if not p.api_key and p.env_key:
                key = self._read_env(p.env_key)
                if key:
                    p.api_key = key

    @property
    def has_any_key(self) -> bool:
        for p in self._providers.values():
            if p.api_key:
                return True
        return False

    @property
    def detected_providers(self) -> list[str]:
        return [pid for pid, p in self._providers.items() if p.api_key]

    # ── 动态添加 Provider/Model ─────────────────────────────────────────

    def add_provider(self, pid: str, name: str, protocol: str = "openai-compatible",
                     api_key: str = "", base_url: str = "") -> Provider:
        """添加或更新一个 provider。"""
        if pid in self._providers:
            p = self._providers[pid]
            p.name = name
            p.protocol = protocol
            if api_key:
                p.api_key = api_key
            if base_url:
                p.base_url = base_url
        else:
            p = Provider(name=name, protocol=protocol, api_key=api_key, base_url=base_url)
            self._providers[pid] = p
        return p

    def add_model(self, pid: str, mid: str, name: str,
                  cost: str = "medium", capability: str = "medium",
                  context: int = 128000, output: int = 16384) -> ModelInfo | None:
        """向 provider 添加一个模型。provider 不存在则返回 None。"""
        p = self._providers.get(pid)
        if not p:
            return None
        m = ModelInfo(provider=pid, model=mid, name=name,
                      cost=cost, capability=capability,
                      context=context, output=output)
        p.models[mid] = m
        return m

    # ── 加载 ────────────────────────────────────────────────────────────

    def _load_defaults(self):
        for pid, p in DEFAULT_PROVIDERS.items():
            self._providers[pid] = Provider(
                name=p.name,
                protocol=p.protocol,
                base_url=p.base_url,
                env_key=p.env_key,
                api_key=self._read_env(p.env_key),
                models={mid: ModelInfo(**vars(m)) for mid, m in p.models.items()},
            )

    def _load_config(self):
        """加载 ~/.sunshine/config.json。"""
        path = Path.home() / ".sunshine" / "config.json"
        if not path.exists():
            return
        self._merge(path)

    def _load_project(self):
        """加载 sunshine.json。"""
        path = Path(self._workspace) / "sunshine.json"
        if path.exists():
            self._merge(path)

    def _merge(self, path: Path):
        """合并配置文件到 providers。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return

        self._default_model = data.get("default_model", self._default_model)
        self._default_provider = data.get("default_provider", self._default_provider)

        # 新版 providers 格式
        for prov_id, prov_data in data.get("providers", {}).items():
            if prov_id not in self._providers:
                self._providers[prov_id] = Provider(
                    name=prov_data.get("name", prov_id),
                    protocol=prov_data.get("protocol", "openai-compatible"),
                )
            p = self._providers[prov_id]
            if "base_url" in prov_data:
                p.base_url = prov_data["base_url"]
            if "api_key" in prov_data:
                p.api_key = prov_data["api_key"]
            if "protocol" in prov_data:
                p.protocol = prov_data["protocol"]
            for mid, mdata in prov_data.get("models", {}).items():
                existing = p.models.get(mid)
                p.models[mid] = ModelInfo(
                    provider=prov_id, model=mid,
                    name=mdata.get("name", existing.name if existing else mid),
                    cost=mdata.get("cost", existing.cost if existing else "medium"),
                    capability=mdata.get("capability", existing.capability if existing else "medium"),
                    context=mdata.get("context", existing.context if existing else 128000),
                    output=mdata.get("output", existing.output if existing else 16384),
                )

        # 旧版扁平格式兼容
        flat_map = {
            "openai_api_key": ("openai", "api_key"),
            "openai_base_url": ("openai", "base_url"),
            "anthropic_api_key": ("anthropic", "api_key"),
            "anthropic_base_url": ("anthropic", "base_url"),
        }
        for flat_key, (prov_id, attr) in flat_map.items():
            value = data.get(flat_key)
            if value is not None and value != "":
                if prov_id not in self._providers:
                    self._providers[prov_id] = Provider(name=prov_id)
                setattr(self._providers[prov_id], attr, value)

    # ── 查询 ────────────────────────────────────────────────────────────

    @property
    def providers(self) -> dict[str, Provider]:
        return self._providers

    @property
    def default_model(self) -> str:
        return self._default_model

    @default_model.setter
    def default_model(self, value: str):
        self._default_model = value
        if value in self._recent:
            self._recent.remove(value)
        self._recent.insert(0, value)
        self._recent = self._recent[:10]

    @property
    def default_provider(self) -> str:
        return self._default_provider

    @default_provider.setter
    def default_provider(self, value: str):
        self._default_provider = value

    def get_provider(self, pid: str) -> Provider | None:
        return self._providers.get(pid)

    def resolve(self, ref: str) -> ModelInfo | None:
        """解析模型引用。支持 "provider/model" 或仅 "model"（模糊匹配）。"""
        if "/" in ref:
            pid, mid = ref.split("/", 1)
            p = self._providers.get(pid)
            return p.resolve_model(mid) if p else None
        for p in self._providers.values():
            m = p.resolve_model(ref)
            if m:
                return m
        return None

    def list_models(self, provider_id: str | None = None) -> list[ModelInfo]:
        """列出模型。可按 provider 过滤。"""
        if provider_id:
            p = self._providers.get(provider_id)
            return list(p.models.values()) if p else []
        result = []
        for p in self._providers.values():
            result.extend(p.models.values())
        return result

    @property
    def recent_models(self) -> list[str]:
        return self._recent

    # ── 持久化 ──────────────────────────────────────────────────────────

    def save(self):
        """保存到 ~/.sunshine/config.json。"""
        path = Path.home() / ".sunshine" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {
            "default_model": self._default_model,
            "default_provider": self._default_provider,
            "providers": {},
            "recent": self._recent,
        }
        for pid, p in self._providers.items():
            data["providers"][pid] = {
                "protocol": p.protocol,
                "base_url": p.base_url,
                "api_key": p.api_key,
            }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def set_key(self, pid: str, key: str):
        if pid in self._providers:
            self._providers[pid].api_key = key

    def set_url(self, pid: str, url: str):
        if pid in self._providers:
            self._providers[pid].base_url = url
