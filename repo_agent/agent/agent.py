from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..environment.base import Environment
from ..features import Features
from ..hooks import Hooks
from ..logging.trajectory import TrajectoryRecorder
from ..models.base import Model
from ..permissions import PermissionPolicy
from ..prompts import load_coding_agent_prompt
from ..runtime import Runtime
from ..schemas import AgentRunResult, Message
from ..verification import VerificationPolicy
from .loop import AgentLoop
from .state import AgentState


@dataclass(slots=True)
class RepoAgentConfig:
    max_steps: int = 50
    context_soft_limit_chars: int = 120_000
    tool_output_limit_chars: int = 30_000
    runs_dir: str | Path = "runs"
    state_dir: str | Path | None = None
    system_prompt: str | None = None
    profile: str = "baseline"
    features: Features | None = None
    permission_mode: str = "trusted"
    model_timeout: float = 120
    model_attempts: int = 3
    max_runtime: float = 1800
    max_workers: int = 3
    child_max_steps: int = 20
    team_idle_timeout: float = 10
    auto_memory: bool = False
    skill_roots: list[str] = field(default_factory=list)
    mcp_config: dict = field(default_factory=dict)
    require_git: bool = True
    verification: VerificationPolicy = field(default_factory=VerificationPolicy)


class RepoAgent:
    """The single runtime entry point for Web, CLI, benchmarks and child agents."""
    def __init__(self, *, model: Model, environment: Environment, config: RepoAgentConfig | None = None,
                 hooks: Hooks | None = None, policy: PermissionPolicy | None = None, fallback_model: Model | None = None):
        self.model, self.environment = model, environment
        self.config = config or RepoAgentConfig()
        self.hooks = hooks or Hooks()
        self.policy = policy or PermissionPolicy(self.config.permission_mode)
        self.fallback_model = fallback_model
        key = hashlib.sha256(str(environment.workspace).encode()).hexdigest()[:16]
        self.state_root = Path(self.config.state_dir or Path(self.config.runs_dir).resolve().parent / ".agent-state" / key).resolve()
        self.last_messages: list[Message] = []
        self.last_runtime: Runtime | None = None

    async def run(self, problem_statement: str, *, instance_id: str = "manual",
                  event_callback: Callable[[dict[str, Any]], None] | None = None,
                  history: list[Message] | None = None, name="lead", parent_runtime=None) -> AgentRunResult:
        if not problem_statement.strip():
            raise ValueError("problem_statement must not be empty")
        if min(self.config.max_steps, self.config.child_max_steps, self.config.max_workers, self.config.model_attempts) < 1:
            raise ValueError("Step, worker and model attempt budgets must be positive")
        if min(self.config.max_runtime, self.config.model_timeout, self.config.context_soft_limit_chars,
               self.config.tool_output_limit_chars) <= 0:
            raise ValueError("Time and context budgets must be positive")
        self.config.verification.validate()
        for path in (Path(self.config.runs_dir).expanduser().resolve(), self.state_root):
            if path.is_relative_to(self.environment.workspace):
                raise ValueError("runs_dir and state_dir must be outside the target workspace")
        public_config = asdict(self.config)
        public_config["mcp_config"] = {"servers": list(self.config.mcp_config.get("mcpServers", {}))}
        public_config.update(workspace=str(self.environment.workspace),
                             base_commit=getattr(self.environment, "base_commit", None))
        recorder = TrajectoryRecorder(runs_dir=self.config.runs_dir, instance_id=instance_id,
                                     model_name=self.model.name, system_prompt=self.config.system_prompt or load_coding_agent_prompt(),
                                     problem_statement=problem_statement, event_callback=event_callback, config=public_config)
        runtime = Runtime(self, recorder, problem_statement, name=name, parent=parent_runtime)
        self.last_runtime = runtime
        state = AgentState(messages=[*(history or []), Message(role="user", content=problem_statement)])
        started = time.perf_counter()
        status, reason, error, patch = "error", "initialization_error", None, ""
        cancelled = False
        try:
            if hasattr(self.environment, "reset_cancellation"):
                self.environment.reset_cancellation()
            async with asyncio.timeout(self.config.max_runtime):
                await runtime.start()
                await self.hooks.emit("user_prompt", {"runtime": runtime, "messages": state.messages})
                status, reason, error = await AgentLoop(runtime).run(state)
                if runtime.features.memory and self.config.auto_memory:
                    try:
                        await asyncio.wait_for(runtime.memory.extract(self.model, state.messages, recorder), self.config.model_timeout)
                    except Exception as exc:
                        recorder.event("memory_extract_error", error=str(exc))
        except asyncio.CancelledError:
            status, reason, cancelled = "terminated", "cancelled", True
        except TimeoutError:
            status, reason = "terminated", "max_runtime"
        except Exception as exc:
            status, reason, error = "error", "runtime_error", f"{type(exc).__name__}: {exc}"
            recorder.event("error", error=error)
        finally:
            try:
                await runtime.close()
            except Exception as exc:
                recorder.event("cleanup_error", error=str(exc))
                status, reason, error = "error", "cleanup_error", str(exc)
            try:
                patch = await asyncio.to_thread(self.environment.get_diff)
            except Exception as exc:
                recorder.event("patch_error", error=str(exc))
                if self.config.require_git:
                    status, reason, error = "error", "patch_error", str(exc)
            self.last_messages = state.messages
            self.last_runtime = runtime
            inputs = state.input_tokens + recorder.aux_input_tokens
            outputs = state.output_tokens + recorder.aux_output_tokens
            result = AgentRunResult(instance_id=instance_id, model_name_or_path=self.model.name,
                                    model_patch=patch, status=status, total_steps=state.step,
                                    total_tool_calls=state.total_tool_calls, input_tokens=inputs,
                                    output_tokens=outputs, total_tokens=inputs + outputs,
                                    runtime=time.perf_counter() - started, run_dir=str(recorder.run_dir),
                                    termination_reason=reason, error=error,
                                    verification=runtime.last_verification or {})
            data = result.to_dict()
            data["children"] = runtime.child_results
            data["aggregate_input_tokens"] = inputs + sum(c["input_tokens"] for c in runtime.child_results)
            data["aggregate_output_tokens"] = outputs + sum(c["output_tokens"] for c in runtime.child_results)
            recorder.event("run_finished", status=status, reason=reason, patch_chars=len(patch))
            recorder.finalize(result=data, patch=patch)
            try:
                await self.hooks.emit("stop", {"runtime": runtime, "result": result})
            except Exception as exc:
                recorder.event("stop_hook_error", error=str(exc))
        if cancelled:
            raise asyncio.CancelledError
        return result
