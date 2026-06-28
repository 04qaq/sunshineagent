"""Pydantic settings for SunshineAgent."""

import json
from importlib import resources
from pathlib import Path

from pydantic_settings import BaseSettings


def _get_prompts_dir() -> str:
    """获取 prompts 目录路径，兼容开发环境和 pip 安装环境。"""
    try:
        ref = resources.files("src.prompts")
    except Exception:
        ref = resources.files("sunshine_agent.prompts")
    return str(ref)


class Settings(BaseSettings):
    model_config = {"env_prefix": "SUNSHINE_", "env_file": ".env", "extra": "ignore"}

    workspace_root: str = str(Path.cwd())
    state_db_path: str = str(Path.cwd() / ".opencode" / "state.db")
    prompts_dir: str = _get_prompts_dir()

    default_agent: str = "build"
    default_provider: str = "anthropic"
    default_model: str = "claude-sonnet-4-6"

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None

    max_steps: int = 100
    tool_call_max_output_tokens: int = 10000


_settings: Settings | None = None


def get_config() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _config_path() -> Path:
    """全局配置文件路径（用户目录，跨项目共享）。"""
    return Path.home() / ".sunshine" / "config.json"


def save_config(settings: Settings):
    """持久化当前配置到 ~/.sunshine/config.json。保留已有数据，只更新 Settings 管理的字段。"""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 读取已有数据，保留 registry 写入的 providers 等字段
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing["default_agent"] = settings.default_agent
    existing["default_provider"] = settings.default_provider
    existing["default_model"] = settings.default_model
    # 旧版兼容（扁平 key）
    existing["openai_api_key"] = settings.openai_api_key
    existing["openai_base_url"] = settings.openai_base_url
    existing["anthropic_api_key"] = settings.anthropic_api_key
    existing["anthropic_base_url"] = settings.anthropic_base_url
    # 确保 providers 键存在，合并 openai/anthropic 的 key/url
    if "providers" not in existing:
        existing["providers"] = {}
    if settings.openai_api_key or settings.openai_base_url:
        existing["providers"].setdefault("openai", {})
        existing["providers"]["openai"]["base_url"] = settings.openai_base_url or ""
        existing["providers"]["openai"]["api_key"] = settings.openai_api_key or ""
    if settings.anthropic_api_key or settings.anthropic_base_url:
        existing["providers"].setdefault("anthropic", {})
        existing["providers"]["anthropic"]["base_url"] = settings.anthropic_base_url or ""
        existing["providers"]["anthropic"]["api_key"] = settings.anthropic_api_key or ""
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def load_config(settings: Settings):
    """从 ~/.sunshine/config.json 恢复配置。文件不存在则静默跳过。"""
    path = _config_path()
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, value in data.items():
        if hasattr(settings, key) and value is not None:
            setattr(settings, key, value)
