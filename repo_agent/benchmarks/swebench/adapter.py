from __future__ import annotations

import re
from typing import Any, Callable

from ...agent.agent import RepoAgent
from ...environment.base import Environment, EnvironmentError
from ...schemas import AgentRunResult
from .schema import SWEbenchTask


class SWEbenchAdapter:
    """Translate a SWE-bench record into the shared RepoAgent input/output contract."""

    def __init__(self, environment: Environment):
        self.environment = environment

    def validate_workspace(self, task: SWEbenchTask) -> None:
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", task.base_commit):
            raise EnvironmentError("base_commit must be a 7-40 character hexadecimal Git commit")
        inside = self.environment.execute("git rev-parse --is-inside-work-tree")
        if inside.exit_code != 0 or inside.stdout.strip() != "true":
            raise EnvironmentError("SWE-bench workspace must be a Git checkout")
        commit = self.environment.execute(f"git cat-file -e {task.base_commit}")
        if commit.exit_code != 0:
            raise EnvironmentError(
                f"base_commit {task.base_commit!r} is not present in the workspace"
            )
        head = self.environment.execute("git rev-parse HEAD")
        expected = self.environment.execute(f"git rev-parse {task.base_commit}")
        if head.stdout.strip() != expected.stdout.strip():
            raise EnvironmentError(
                "Workspace HEAD does not match task base_commit. Prepare an isolated checkout "
                "at the exact base commit before running the agent."
            )
        dirty = self.environment.execute("git status --porcelain")
        if dirty.stdout.strip():
            raise EnvironmentError("SWE-bench workspace must be clean before the run")

    async def run(
        self,
        task: SWEbenchTask,
        agent: RepoAgent,
        *,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentRunResult:
        self.validate_workspace(task)
        return await agent.run(
            task.problem_statement,
            instance_id=task.instance_id,
            event_callback=event_callback,
        )

    @staticmethod
    def prediction(result: AgentRunResult) -> dict[str, str]:
        return {
            "instance_id": result.instance_id,
            "model_name_or_path": result.model_name_or_path,
            "model_patch": result.model_patch,
        }
