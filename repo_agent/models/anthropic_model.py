from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from ..schemas import Message, ModelResponse, ToolCall, Usage
from .base import Model


class AnthropicModel(Model):
    """Anthropic Messages API implementation of the provider-neutral Model API."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 4096,
    ):
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        elif base_url:
            kwargs["api_key"] = "not-needed"
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncAnthropic(**kwargs)
        self._name = model
        self.max_tokens = max_tokens

    @property
    def name(self) -> str:
        return self._name

    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> ModelResponse:
        response = await self.client.messages.create(
            model=self.name,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=_to_anthropic_messages(messages),
            tools=tools,
        )
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
                )
        return ModelResponse(
            content="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            usage=Usage(
                input_tokens=int(getattr(response.usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(response.usage, "output_tokens", 0) or 0),
            ),
        )


def _to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role in {"user", "summary"}:
            content = message.content
            if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], str):
                converted[-1]["content"] += "\n\n" + content
            else:
                converted.append({"role": "user", "content": content})
        elif message.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
                for call in message.tool_calls
            )
            converted.append({"role": "assistant", "content": blocks or " "})
        elif message.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
                "is_error": message.is_error,
            }
            if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
    return converted
