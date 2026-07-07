from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Role = Literal["user", "assistant", "assistant_tool_call", "tool_result", "context_summary"]


class ToolCall(BaseModel):
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool = False


class AgentEvent(BaseModel):
    type: Literal[
        "session",
        "user",
        "assistant",
        "tool_start",
        "tool_result",
        "todo",
        "compact",
        "error",
        "done",
    ]
    content: str = ""
    session_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    input: dict[str, Any] | None = None
    is_error: bool = False
    data: Any | None = None


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    messages: list[ChatMessage]


class SessionSummary(BaseModel):
    session_id: str
    message_count: int
    todos: list[dict[str, str]]


class ModelResult(BaseModel):
    assistant_text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str | None = None
