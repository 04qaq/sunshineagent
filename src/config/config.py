"""Pydantic settings for SunshineAgent."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "SUNSHINE_", "env_file": ".env"}

    workspace_root: str = str(Path.cwd())
    state_db_path: str = str(Path.cwd() / ".opencode" / "state.db")
    prompts_dir: str = str(Path(__file__).parent.parent.parent / "prompts")

    default_agent: str = "build"
    default_provider: str = "anthropic"
    default_model: str = "claude-sonnet-4-6"

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    anthropic_api_key: str | None = None

    max_steps: int = 100
    tool_call_max_output_tokens: int = 10000


_settings: Settings | None = None


def get_config() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
