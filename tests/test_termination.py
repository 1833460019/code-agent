from __future__ import annotations

import asyncio
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from repo_agent import LocalEnvironment, RepoAgent, RepoAgentConfig
from repo_agent.models.base import Model
from repo_agent.schemas import Message, ModelResponse, ToolCall


class NeverFinishModel(Model):
    @property
    def name(self) -> str:
        return "never-finish"

    async def complete(self, *, system_prompt: str, messages: list[Message], tools: list[dict]) -> ModelResponse:
        return ModelResponse(
            tool_calls=[ToolCall("write", "write_file", {"path": "partial.txt", "content": "saved\n"})]
        )


class TerminationTest(unittest.TestCase):
    def test_max_steps_still_saves_partial_patch(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            agent = RepoAgent(
                model=NeverFinishModel(),
                environment=LocalEnvironment(root),
                config=RepoAgentConfig(max_steps=1, runs_dir=root.parent / "runs"),
            )
            result = asyncio.run(agent.run("Create the requested file.", instance_id=uuid.uuid4().hex))
            self.assertEqual(result.status, "terminated")
            self.assertEqual(result.termination_reason, "max_steps")
            self.assertIn("partial.txt", result.model_patch)
            self.assertTrue((Path(result.run_dir) / "patch.diff").is_file())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_rejects_artifacts_inside_target_workspace(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            agent = RepoAgent(
                model=NeverFinishModel(),
                environment=LocalEnvironment(root),
                config=RepoAgentConfig(max_steps=1, runs_dir=root / "runs"),
            )
            with self.assertRaises(ValueError):
                asyncio.run(agent.run("Do not pollute the patch."))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
