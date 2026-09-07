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
    raise ValueError(
        f"Unsupported provider {provider!r}. Implement repo_agent.models.base.Model "
        "and register it in create_model."
    )
