from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


RunStatus = Literal["success", "terminated", "error"]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class ModelResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage: Usage = field(default_factory=Usage)


@dataclass(slots=True)
class Message:
    role: Literal["user", "assistant", "tool", "summary"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool = False


@dataclass(slots=True)
class CommandResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    timed_out: bool = False

    @property
    def output(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout.rstrip())
        if self.stderr:
            parts.append(self.stderr.rstrip())
        return "\n".join(parts) or "(no output)"


@dataclass(slots=True)
class ToolResult:
    ok: bool
    output: str
    finished: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRunResult:
    instance_id: str
    model_name_or_path: str
    model_patch: str
    status: RunStatus
    total_steps: int
    total_tool_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    runtime: float
    run_dir: str
    termination_reason: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["success"] = self.status == "success"
        data["terminated"] = self.status == "terminated"
        return data
