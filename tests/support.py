from __future__ import annotations
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from repo_agent import LocalEnvironment, RepoAgent, RepoAgentConfig
from repo_agent.models.base import Model
from repo_agent.schemas import ModelResponse, ToolCall, Usage


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True)
        self.git("init")
        self.git("config", "user.name", "Runtime Test")
        self.git("config", "user.email", "runtime@example.test")
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "base")

    def tearDown(self):
        expected = Path(__file__).resolve().parent / ".work"
        assert self.root.resolve().is_relative_to(expected.resolve()) and self.root != expected
        shutil.rmtree(self.root, ignore_errors=True)

    def git(self, *args, **kwargs):
        if isinstance(kwargs.get("input"), str):
            kwargs["input"] = kwargs["input"].encode("utf-8")
        result = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, **kwargs)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.decode("utf-8").strip()

    def agent(self, model, **kwargs):
        return RepoAgent(model=model, environment=LocalEnvironment(self.repo), config=RepoAgentConfig(
            runs_dir=self.root / "runs", state_dir=self.root / "state", **kwargs))


class SequenceModel(Model):
    def __init__(self, sequence):
        self.sequence = iter(sequence)
        self.requests = []

    @property
    def name(self):
        return "sequence-test-model"

    async def complete(self, **kwargs):
        self.requests.append(kwargs)
        item = next(self.sequence)
        if isinstance(item, Exception):
            raise item
        return item


def call(name, /, **arguments):
    return ModelResponse(tool_calls=[ToolCall(uuid.uuid4().hex, name, arguments)], usage=Usage(10, 5))


def observations(messages, name):
    return [m.content for m in messages if m.role == "tool" and m.tool_name == name]
