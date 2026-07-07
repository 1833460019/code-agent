from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic

from .config import Settings
from .schemas import ChatMessage, ModelResult, ToolCall
from .tools import ToolDefinition


SYSTEM_PROMPT = """You are code-agent, a practical coding agent inspired by Claude Code.

Work like a real coding agent:
1. Understand the user's request and inspect the workspace before making claims.
2. Use tools in a loop: call tools, read tool results, decide the next concrete action.
3. Use TodoWrite or plan_update for multi-step work, and keep the active item current.
4. Use task_create/task_list/task_update for durable work items that should survive a turn.
5. Use skill_list and load_skill when a specialized workflow may apply.
6. Use memory_read/memory_search before relying on remembered project facts, and memory_write for durable preferences or discoveries.
7. Use background_run for long checks, servers, or builds; continue with background_check/background results.
8. Use subagent to delegate isolated exploration as a persistent subagent task record.
9. Use compact when context is too noisy.
10. Finish with a concise final answer when the task is complete.

Safety and workspace rules:
- Keep file operations inside the configured workspace.
- Prefer focused edits over broad rewrites.
- Explain completed work clearly, including files touched and verification.
- If a tool fails, adapt and continue instead of stopping at the first error.
"""


class ModelAdapter:
    async def next(self, messages: list[ChatMessage], tools: list[ToolDefinition]) -> ModelResult:
        raise NotImplementedError


class MockModelAdapter(ModelAdapter):
    async def next(self, messages: list[ChatMessage], tools: list[ToolDefinition]) -> ModelResult:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        tool_names = ", ".join(tool.name for tool in tools)
        return ModelResult(
            assistant_text=(
                "Model is not configured yet. Set ANTHROPIC_API_KEY and MODEL_ID in backend/.env, "
                "then restart the API.\n\n"
                f"I received: {last_user}\n\nAvailable local tools: {tool_names}"
            ),
            stop_reason="mock",
        )


class AnthropicModelAdapter(ModelAdapter):
    def __init__(self, settings: Settings):
        kwargs: dict[str, Any] = {}
        if settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key
        elif settings.anthropic_base_url:
            kwargs["api_key"] = "not-needed"
        if settings.anthropic_base_url:
            kwargs["base_url"] = settings.anthropic_base_url
        self.client = AsyncAnthropic(**kwargs)
        self.settings = settings

    async def next(self, messages: list[ChatMessage], tools: list[ToolDefinition]) -> ModelResult:
        response = await self.client.messages.create(
            model=self.settings.model_id,
            max_tokens=self.settings.max_tokens,
            system=SYSTEM_PROMPT,
            messages=self._to_anthropic_messages(messages),
            tools=[
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ],
        )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input or {})))
        return ModelResult(
            assistant_text="\n".join(part for part in text_parts if part).strip(),
            tool_calls=calls,
            stop_reason=response.stop_reason,
        )

    def _to_anthropic_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        pending_assistant_blocks: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_assistant_blocks() -> None:
            nonlocal pending_assistant_blocks
            if pending_assistant_blocks:
                converted.append({"role": "assistant", "content": pending_assistant_blocks})
                pending_assistant_blocks = []

        def flush_tool_results() -> None:
            nonlocal pending_tool_results
            if pending_tool_results:
                flush_assistant_blocks()
                converted.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []

        for message in messages:
            if message.role == "tool_result":
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": message.content,
                        "is_error": message.is_error,
                    }
                )
                continue

            flush_tool_results()

            if message.role in ("user", "context_summary"):
                flush_assistant_blocks()
                converted.append({"role": "user", "content": message.content})
            elif message.role == "assistant":
                pending_assistant_blocks.append({"type": "text", "text": message.content or " "})
            elif message.role == "assistant_tool_call":
                pending_assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": message.tool_call_id,
                        "name": message.tool_name,
                        "input": self._parse_tool_input(message.content),
                    }
                )

        flush_tool_results()
        flush_assistant_blocks()
        return converted

    @staticmethod
    def _parse_tool_input(content: str) -> dict[str, Any]:
        try:
            value = json.loads(content or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


def create_model_adapter(settings: Settings) -> ModelAdapter:
    if settings.anthropic_api_key or settings.anthropic_base_url:
        return AnthropicModelAdapter(settings)
    return MockModelAdapter()
