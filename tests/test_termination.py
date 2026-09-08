from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from repo_agent import LocalEnvironment, RepoAgent, RepoAgentConfig, VerificationPolicy
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


class RetryFinishModel(Model):
    def __init__(self):
        self.calls = 0

    @property
    def name(self) -> str:
        return "retry-finish"

    async def complete(self, **kwargs) -> ModelResponse:
        self.calls += 1
        calls = {
            1: ToolCall("finish-early", "finish", {"summary": "done"}),
            2: ToolCall("write", "write_file", {"path": "fixed.txt", "content": "fixed\n"}),
            3: ToolCall("finish-final", "finish", {"summary": "verified"}),
        }
        return ModelResponse(tool_calls=[calls[self.calls]])


class TerminationTest(unittest.TestCase):
    def test_finish_gate_rejects_empty_patch_and_persists_evidence(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            agent = RepoAgent(
                model=RetryFinishModel(),
                environment=LocalEnvironment(root),
                config=RepoAgentConfig(
                    max_steps=4,
                    runs_dir=root.parent / "runs",
                    verification=VerificationPolicy(
                        require_patch=True,
                        commands=["git diff --check"],
                    ),
                ),
            )
            result = asyncio.run(agent.run("Create a real patch before finishing."))
            self.assertEqual(result.status, "success")
            self.assertTrue(result.verification["accepted"])
            evidence = json.loads(
                (Path(result.run_dir) / "verification.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(evidence["attempts"]), 2)
            self.assertFalse(evidence["attempts"][0]["accepted"])
            self.assertIn("final diff is empty", evidence["attempts"][0]["failures"][0])
            self.assertEqual(evidence["attempts"][1]["commands"][0]["exit_code"], 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)

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
