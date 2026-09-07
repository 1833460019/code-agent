from __future__ import annotations

import json
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from repo_agent.benchmarks.swebench import SWEbenchAdapter, SWEbenchTask, load_task
from repo_agent.environment import EnvironmentError, LocalEnvironment


class SWEbenchAdapterTest(unittest.TestCase):
    def test_loads_one_task_from_list_and_validates_exact_head(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "agent@example.test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Agent Test"], cwd=root, check=True)
            (root / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
            ).stdout.strip()
            task_file = root.parent / f"{uuid.uuid4().hex}.json"
            task_file.write_text(
                json.dumps([
                    {
                        "instance_id": "demo__one-1",
                        "repo": "demo/one",
                        "base_commit": head,
                        "problem_statement": "Fix it.",
                    }
                ]),
                encoding="utf-8",
            )
            task = load_task(task_file, instance_id="demo__one-1")
            SWEbenchAdapter(LocalEnvironment(root)).validate_workspace(task)
            self.assertEqual(task.base_commit, head)
            task_file.unlink()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_rejects_non_hex_commit_before_shell_interpolation(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            task = SWEbenchTask("bad", "demo/repo", "HEAD; echo unsafe", "Fix it")
            with self.assertRaises(EnvironmentError):
                SWEbenchAdapter(LocalEnvironment(root)).validate_workspace(task)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
