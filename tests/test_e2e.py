from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import unittest
import uuid
import sys
from pathlib import Path

from repo_agent import LocalEnvironment, RepoAgent, RepoAgentConfig
from repo_agent.models.base import Model
from repo_agent.schemas import Message, ModelResponse, ToolCall, Usage


class ScriptedModel(Model):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "scripted-e2e-model"

    async def complete(self, *, system_prompt: str, messages: list[Message], tools: list[dict]) -> ModelResponse:
        self.calls += 1
        sequences = {
            1: ToolCall("call-1", "read_file", {"path": "calculator.py"}),
            2: ToolCall(
                "call-2",
                "edit_file",
                {"path": "calculator.py", "old_text": "return left - right", "new_text": "return left + right"},
            ),
            3: ToolCall("call-3", "shell", {"command": f'"{sys.executable}" -m unittest -q'}),
            4: ToolCall("call-4", "shell", {"command": "git diff --check"}),
            5: ToolCall("call-5", "finish", {"summary": "Fixed add() and verified tests."}),
        }
        return ModelResponse(
            content="",
            tool_calls=[sequences[self.calls]],
            stop_reason="tool_use",
            usage=Usage(input_tokens=10, output_tokens=5),
        )


class EndToEndRunTest(unittest.TestCase):
    def test_agent_generates_patch_and_trajectory(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            workspace = root / "repo"
            runs = root / "runs"
            workspace.mkdir()
            (workspace / "calculator.py").write_text(
                "def add(left: int, right: int) -> int:\n    return left - right\n",
                encoding="utf-8",
            )
            (workspace / "test_calculator.py").write_text(
                "import unittest\nfrom calculator import add\n\n"
                "class CalculatorTest(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
                encoding="utf-8",
            )
            (workspace / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
            _git(workspace, "init")
            _git(workspace, "config", "user.email", "agent@example.test")
            _git(workspace, "config", "user.name", "Agent Test")
            _git(workspace, "add", ".")
            _git(workspace, "commit", "-m", "base")
            before = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=workspace, capture_output=True)
            self.assertNotEqual(before.returncode, 0, "Bug reproduction must fail before the agent runs")

            agent = RepoAgent(
                model=ScriptedModel(),
                environment=LocalEnvironment(workspace),
                config=RepoAgentConfig(max_steps=10, runs_dir=runs),
            )
            result = asyncio.run(agent.run("Fix add(): it subtracts instead of adding.", instance_id="demo-1"))

            self.assertEqual(result.status, "success")
            self.assertIn("return left + right", result.model_patch)
            self.assertEqual(result.total_steps, 5)
            self.assertEqual(result.total_tool_calls, 5)
            self.assertEqual(result.total_tokens, 75)
            run_dir = Path(result.run_dir)
            for name in ("trajectory.json", "result.json", "patch.diff", "run.log"):
                self.assertTrue((run_dir / name).is_file(), name)
            trajectory = json.loads((run_dir / "trajectory.json").read_text(encoding="utf-8"))
            self.assertEqual(len(trajectory["steps"]), 5)
            self.assertEqual(trajectory["final_status"], "success")
            test_call = trajectory["steps"][2]["tool_calls"][0]
            self.assertTrue(test_call["ok"], test_call["observation"])
            self.assertEqual(test_call["metadata"]["exit_code"], 0)
            self.assertTrue(trajectory["steps"][3]["tool_calls"][0]["ok"])
            applied = subprocess.run(["git", "apply", "--check", "--reverse", "-"], cwd=workspace,
                                     input=result.model_patch, capture_output=True, text=True)
            self.assertEqual(applied.returncode, 0, applied.stderr)
        finally:
            shutil.rmtree(root, ignore_errors=True)


def _git(workspace: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=workspace, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
