from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from ..schemas import Message, ModelResponse, ToolCall, Usage
from .base import Model


class OpenAIModel(Model):
    """OpenAI-compatible Chat Completions adapter, including function tools."""

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
            kwargs["base_url"] = base_url.rstrip("/")
        self.client = AsyncOpenAI(**kwargs)
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
        response = await self.client.chat.completions.create(
            model=self.name,
            max_tokens=self.max_tokens,
            messages=[{"role": "system", "content": system_prompt}, *_to_openai_messages(messages)],
            tools=[_to_openai_tool(tool) for tool in tools] or None,
            tool_choice="auto" if tools else None,
        )
        choice = response.choices[0]
        tool_calls = []
        for call in choice.message.tool_calls or []:
            arguments = json.loads(call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError(f"Tool arguments for {call.function.name} must decode to an object")
            tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        usage = response.usage
        return ModelResponse(
            content=choice.message.content or "",
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason,
            usage=Usage(
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            ),
        )


def _to_openai_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        },
    }


def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role in {"user", "summary"}:
            converted.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            payload: dict[str, Any] = {"role": "assistant", "content": message.content or None}
            if message.tool_calls:
                payload["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                    }
                    for call in message.tool_calls
                ]
            converted.append(payload)
        elif message.role == "tool":
            converted.append({
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            })
    return converted
