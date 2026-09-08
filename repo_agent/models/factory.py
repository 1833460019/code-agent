from __future__ import annotations

import os

from .base import Model


def create_model(
    *,
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 4096,
) -> Model:
    if provider.lower() == "anthropic":
        from .anthropic_model import AnthropicModel

        return AnthropicModel(
            model,
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
            base_url=base_url or os.getenv("ANTHROPIC_BASE_URL"),
            max_tokens=max_tokens,
        )
    if provider.lower() in {"openai", "siliconflow"}:
        from .openai_model import OpenAIModel

        siliconflow = provider.lower() == "siliconflow"
        return OpenAIModel(
            model,
            api_key=api_key or os.getenv("SILICONFLOW_API_KEY" if siliconflow else "OPENAI_API_KEY")
            or (os.getenv("ANTHROPIC_API_KEY") if siliconflow else None),
            base_url=base_url
            or os.getenv("OPENAI_BASE_URL")
            or ("https://api.siliconflow.cn/v1" if siliconflow else None),
            max_tokens=max_tokens,
        )
    raise ValueError(
        f"Unsupported provider {provider!r}. Implement repo_agent.models.base.Model "
        "and register it in create_model."
    )
