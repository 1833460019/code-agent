from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .config import Settings
from .model import ModelAdapter
from .schemas import AgentEvent, ChatMessage
from .tools import ToolContext, create_tool_registry, drain_background_notifications


@dataclass
class AgentSession:
    session_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    todos: list[dict[str, str]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class AgentKernel:
    def __init__(self, settings: Settings, model: ModelAdapter):
        self.settings = settings
        self.model = model
        self.tools = create_tool_registry()
        self._tool_by_name = {tool.name: tool for tool in self.tools}
        self.sessions: dict[str, AgentSession] = {}

    def create_session(self) -> AgentSession:
        session = AgentSession(session_id=str(uuid.uuid4()))
        self.sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str | None) -> AgentSession:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        return self.create_session()

    def list_sessions(self) -> list[AgentSession]:
        return sorted(self.sessions.values(), key=lambda session: session.updated_at, reverse=True)

    async def run_turn(self, session_id: str | None, user_message: str) -> AsyncIterator[AgentEvent]:
        session = self.get_session(session_id)
        session.updated_at = time.time()
        yield AgentEvent(type="session", session_id=session.session_id)

        session.messages.append(ChatMessage(role="user", content=user_message))
        yield AgentEvent(type="user", session_id=session.session_id, content=user_message)

        rounds_without_todo = 0
        for _step in range(self.settings.max_agent_steps):
            compacted = self._compact_if_needed(session)
            if compacted:
                yield AgentEvent(
                    type="compact",
                    session_id=session.session_id,
                    content=compacted.content,
                    data={"message_count": len(session.messages)},
                )

            try:
                result = await self.model.next(session.messages, self.tools)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                session.messages.append(ChatMessage(role="assistant", content=message, is_error=True))
                yield AgentEvent(type="error", session_id=session.session_id, content=message, is_error=True)
                yield AgentEvent(type="done", session_id=session.session_id)
                return

            if result.assistant_text:
                session.messages.append(ChatMessage(role="assistant", content=result.assistant_text))
                yield AgentEvent(type="assistant", session_id=session.session_id, content=result.assistant_text)

            if not result.tool_calls:
                yield AgentEvent(type="done", session_id=session.session_id)
                return

            used_todo = False
            for call in result.tool_calls:
                session.messages.append(
                    ChatMessage(
                        role="assistant_tool_call",
                        content=json.dumps(call.input, ensure_ascii=False),
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )
                )
                yield AgentEvent(
                    type="tool_start",
                    session_id=session.session_id,
                    tool_name=call.name,
                    tool_call_id=call.id,
                    input=call.input,
                )

                tool_result = self._execute_tool(call.name, call.input, session)
                session.messages.append(
                    ChatMessage(
                        role="tool_result",
                        content=tool_result.output,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        is_error=not tool_result.ok,
                    )
                )
                yield AgentEvent(
                    type="tool_result",
                    session_id=session.session_id,
                    tool_name=call.name,
                    tool_call_id=call.id,
                    content=tool_result.output,
                    is_error=not tool_result.ok,
                    data=tool_result.data,
                )

                if call.name == "TodoWrite":
                    used_todo = True
                    yield AgentEvent(type="todo", session_id=session.session_id, content="Todo list updated.", data=session.todos)

                if call.name == "compact" and tool_result.ok:
                    summary = self._compact(session, focus=str(call.input.get("focus", "")))
                    yield AgentEvent(type="compact", session_id=session.session_id, content=summary.content, data={"message_count": len(session.messages)})

            rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
            if session.todos and rounds_without_todo >= 3:
                session.messages.append(ChatMessage(role="user", content="<reminder>Update your todos.</reminder>"))
                rounds_without_todo = 0

        message = "Reached the maximum agent step limit for this turn."
        session.messages.append(ChatMessage(role="assistant", content=message))
        yield AgentEvent(type="assistant", session_id=session.session_id, content=message)
        yield AgentEvent(type="done", session_id=session.session_id)

    def _execute_tool(self, name: str, input_data: dict, session: AgentSession):
        tool = self._tool_by_name.get(name)
        if not tool:
            from .tools import ToolResult

            return ToolResult(ok=False, output=f"Unknown tool: {name}")
        context = ToolContext(workspace=self.settings.workspace_dir, settings=self.settings, todos=session.todos)
        return tool.handler(input_data, context)

    def _estimate_context_chars(self, session: AgentSession) -> int:
        return sum(len(message.content) + 80 for message in session.messages)

    def _compact_if_needed(self, session: AgentSession) -> ChatMessage | None:
        if self._estimate_context_chars(session) <= self.settings.context_soft_limit_chars:
            return None
        return self._compact(session)

    def _compact(self, session: AgentSession, focus: str = "") -> ChatMessage:
        if len(session.messages) <= 16:
            summary = ChatMessage(role="context_summary", content="Context is already short.")
            session.messages.insert(0, summary)
            return summary

        keep_recent = session.messages[-12:]
        older = session.messages[:-12]
        lines = ["Earlier conversation was compacted.", f"Compressed messages: {len(older)}"]
        if focus:
            lines.append(f"Focus: {focus}")
        for message in older[-20:]:
            detail = f" ({message.tool_name})" if message.tool_name else ""
            lines.append(f"- {message.role}{detail}: {message.content[:500]}")
        summary = ChatMessage(role="context_summary", content="\n".join(lines))
        session.messages = [summary, *keep_recent]
        return summary

