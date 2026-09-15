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
    enable_thinking: bool | None = None,
    reasoning_effort: str | None = None,
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
        extra_body = {}
        if siliconflow and enable_thinking is not None:
            extra_body["enable_thinking"] = enable_thinking
        if siliconflow and reasoning_effort:
            extra_body["reasoning_effort"] = reasoning_effort
        return OpenAIModel(
            model,
            api_key=api_key or os.getenv("SILICONFLOW_API_KEY" if siliconflow else "OPENAI_API_KEY")
            or (os.getenv("ANTHROPIC_API_KEY") if siliconflow else None),
            base_url=base_url
            or os.getenv("OPENAI_BASE_URL")
            or ("https://api.siliconflow.cn/v1" if siliconflow else None),
            max_tokens=max_tokens,
            extra_body=extra_body,
        )
    raise ValueError(
        f"Unsupported provider {provider!r}. Implement repo_agent.models.base.Model "
        "and register it in create_model."
    )
