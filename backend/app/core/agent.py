"""Web session/SSE adapter. All execution is delegated to repo_agent.RepoAgent."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from repo_agent import LocalEnvironment, RepoAgent, RepoAgentConfig
from repo_agent.permissions import PermissionPolicy
from repo_agent.schemas import Message, ToolCall
from repo_agent.storage import atomic_json, identifier
from repo_agent.scheduler import CronScheduler
from repo_agent.hooks import Hooks

from .schemas import AgentEvent, ChatMessage


@dataclass
class AgentSession:
    session_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    todos: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def title(self):
        return next((m.content.replace("\n", " ")[:80] for m in self.messages if m.role == "user"), "New session")

    def to_dict(self):
        return dict(session_id=self.session_id, messages=[m.model_dump() for m in self.messages],
                    todos=self.todos, created_at=self.created_at, updated_at=self.updated_at)


class AgentKernel:
    def __init__(self, settings, model):
        self.settings, self.model = settings, model
        self.sessions: dict[str, AgentSession] = {}
        self.state_dir = Path(settings.state_dir or Path(__file__).resolve().parents[3] / ".agent-state" / "web").resolve()
        self.sessions_dir = self.state_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.approvals: dict[str, asyncio.Future] = {}
        self._workspace_lock = asyncio.Lock()
        self._load_sessions()

    def _load_sessions(self):
        # Import prior playground sessions once, preserving user conversations.
        legacy = self.settings.workspace_dir / ".sessions"
        for directory in (legacy, self.sessions_dir):
            for path in directory.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    key = identifier(data["session_id"])
                    self.sessions[key] = AgentSession(key,
                        [ChatMessage.model_validate(m) for m in data.get("messages", [])],
                        data.get("todos", []), data.get("created_at", time.time()), data.get("updated_at", time.time()))
                except (ValueError, KeyError, OSError):
                    continue

    def _save_session(self, session):
        session.updated_at = time.time()
        atomic_json(self.sessions_dir / f"{identifier(session.session_id)}.json", session.to_dict())

    def get_session(self, session_id=None):
        if session_id:
            identifier(session_id)
            if session_id in self.sessions:
                return self.sessions[session_id]
        key = uuid.uuid4().hex
        session = AgentSession(key)
        self.sessions[key] = session
        self._save_session(session)
        return session

    def list_sessions(self):
        return sorted(self.sessions.values(), key=lambda s: s.updated_at, reverse=True)

    def list_runs(self):
        root = self.state_dir / "runs"
        runs = []
        for path in root.glob("*/result.json"):
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
                runs.append({
                    "run_id": path.parent.name,
                    "instance_id": result.get("instance_id", path.parent.name),
                    "status": result.get("status", "unknown"),
                    "termination_reason": result.get("termination_reason", ""),
                    "total_steps": result.get("total_steps", 0),
                    "total_tokens": result.get("total_tokens", 0),
                    "runtime": result.get("runtime", 0),
                    "updated_at": path.stat().st_mtime,
                })
            except (OSError, ValueError):
                continue
        return sorted(runs, key=lambda run: run["updated_at"], reverse=True)

    def get_run(self, run_id):
        directory = (self.state_dir / "runs" / identifier(run_id)).resolve()
        runs_root = (self.state_dir / "runs").resolve()
        if not directory.is_relative_to(runs_root) or not (directory / "result.json").is_file():
            raise KeyError(run_id)
        data = {}
        for name in ("result", "trajectory", "verification"):
            path = directory / f"{name}.json"
            data[name] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        patch = directory / "patch.diff"
        data["patch"] = patch.read_text(encoding="utf-8") if patch.is_file() else ""
        data["run_id"] = run_id
        return data

    def resolve_approval(self, request_id, approved):
        future = self.approvals.get(request_id)
        if future is None or future.done():
            raise KeyError("Approval expired or already answered")
        future.set_result(approved)

    async def run_turn(self, session_id, user_message):
        session = self.get_session(session_id)
        yield AgentEvent(type="session", session_id=session.session_id)
        queue: asyncio.Queue = asyncio.Queue()
        yield AgentEvent(type="user", session_id=session.session_id, content=user_message)

        async def approve(name, arguments):
            key = uuid.uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self.approvals[key] = future
            queue.put_nowait(AgentEvent(type="approval_required", session_id=session.session_id,
                tool_name=name, input=arguments, content="Tool permission required", data={"request_id": key}))
            try:
                return await asyncio.wait_for(future, 180)
            except TimeoutError:
                return False
            finally:
                self.approvals.pop(key, None)

        def on_event(event):
            kind = event["type"]
            if kind not in {"assistant", "tool_start", "tool_result", "todo", "compact", "error"}:
                return
            message = AgentEvent(type=kind, session_id=session.session_id,
                content=str(event.get("content", event.get("model_observation", event.get("observation", event.get("error", ""))))),
                tool_name=event.get("tool_name"), tool_call_id=event.get("tool_call_id"),
                input=event.get("arguments"), is_error=not event.get("ok", True), data=event.get("data"))
            if kind == "assistant":
                session.messages.append(ChatMessage(role="assistant", content=message.content))
            elif kind == "tool_start":
                session.messages.append(ChatMessage(role="assistant_tool_call", content=json.dumps(message.input),
                    tool_name=message.tool_name, tool_call_id=message.tool_call_id))
            elif kind == "tool_result":
                session.messages.append(ChatMessage(role="tool_result", content=message.content,
                    tool_name=message.tool_name, tool_call_id=message.tool_call_id, is_error=message.is_error))
            elif kind == "todo":
                session.todos = event["data"]
            self._save_session(session)
            queue.put_nowait(message)

        async def produce():
            try:
                async with self._workspace_lock:
                    history = _to_runtime(session.messages)
                    session.messages.append(ChatMessage(role="user", content=user_message))
                    self._save_session(session)
                    config = self.runtime_config()
                    hooks = Hooks()
                    hooks.register("user_prompt", lambda p: p["runtime"].todos.update(session.todos))
                    agent = RepoAgent(model=self.model, environment=LocalEnvironment(self.settings.workspace_dir,
                        command_timeout=self.settings.command_timeout_seconds), config=config, hooks=hooks,
                        policy=PermissionPolicy(self.settings.permission_mode, approver=approve))
                    result = await agent.run(user_message, instance_id=session.session_id, history=history, event_callback=on_event)
                    queue.put_nowait(AgentEvent(type="done", session_id=session.session_id, data=result.to_dict()))
            except Exception as exc:
                queue.put_nowait(AgentEvent(type="error", content=str(exc), is_error=True))
            finally:
                queue.put_nowait(None)
        producer = asyncio.create_task(produce())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    def runtime_config(self):
        mcp = json.loads(self.settings.mcp_config_file.read_text(encoding="utf-8")) if self.settings.mcp_config_file else {}
        return RepoAgentConfig(profile="full", max_steps=self.settings.max_agent_steps,
            runs_dir=self.state_dir / "runs", state_dir=self.state_dir / "services",
            require_git=False, permission_mode=self.settings.permission_mode,
            context_soft_limit_chars=self.settings.context_soft_limit_chars,
            tool_output_limit_chars=self.settings.tool_output_limit_chars,
            skill_roots=self.settings.skill_roots, mcp_config=mcp, auto_memory=self.settings.auto_memory)

    async def serve_schedules(self, stop):
        scheduler = CronScheduler(self.state_dir / "services")
        async def consume(entry):
            async with self._workspace_lock:
                # There is no live approval UI for unattended jobs. Ask policies fail closed;
                # trusted execution must be explicitly configured by the operator.
                agent = RepoAgent(model=self.model, environment=LocalEnvironment(self.settings.workspace_dir,
                    command_timeout=self.settings.command_timeout_seconds), config=self.runtime_config())
                result = await agent.run(entry["prompt"], instance_id="cron-" + entry["id"])
                return result.to_dict()
        await scheduler.serve(consume, stop)


def _to_runtime(messages):
    result = []
    for message in messages:
        if message.role == "assistant_tool_call":
            if not result or result[-1].role != "assistant":
                result.append(Message(role="assistant"))
            # Historical Web logs interleaved calls/results; each remains a valid turn.
            result[-1].tool_calls.append(ToolCall(message.tool_call_id, message.tool_name, json.loads(message.content)))
        elif message.role == "tool_result":
            result.append(Message(role="tool", content=message.content, tool_call_id=message.tool_call_id,
                                  tool_name=message.tool_name, is_error=message.is_error))
        else:
            result.append(Message(role="summary" if message.role == "context_summary" else message.role, content=message.content))
    # Discard an incomplete last tool turn left by interruption before continuing.
    outstanding = set()
    safe_end = 0
    for index, message in enumerate(result):
        outstanding.update(c.id for c in message.tool_calls)
        if message.role == "tool":
            outstanding.discard(message.tool_call_id)
        if not outstanding:
            safe_end = index + 1
    return result[:safe_end]
