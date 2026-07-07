from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "code-agent"
    model_id: str = Field(default="claude-3-5-sonnet-latest", alias="MODEL_ID")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str | None = Field(default=None, alias="ANTHROPIC_BASE_URL")
    max_agent_steps: int = Field(default=20, alias="MAX_AGENT_STEPS")
    max_tokens: int = Field(default=4096, alias="MAX_TOKENS")
    context_soft_limit_chars: int = Field(default=120_000, alias="CONTEXT_SOFT_LIMIT_CHARS")
    tool_output_limit_chars: int = Field(default=24_000, alias="TOOL_OUTPUT_LIMIT_CHARS")
    command_timeout_seconds: int = Field(default=60, alias="COMMAND_TIMEOUT_SECONDS")
    workspace_dir: Path = Field(default=Path("../workspace"), alias="AGENT_WORKSPACE")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.workspace_dir.is_absolute():
        backend_dir = Path(__file__).resolve().parents[2]
        settings.workspace_dir = (backend_dir / settings.workspace_dir).resolve()
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    return settings
