from __future__ import annotations

import json
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from repo_agent.benchmarks.swebench import (
    BatchStore,
    SWEbenchAdapter,
    SWEbenchTask,
    WorkspacePool,
    load_task,
    load_tasks,
)
from repo_agent.environment import EnvironmentError, LocalEnvironment


class SWEbenchAdapterTest(unittest.TestCase):
    def test_batch_store_resumes_and_writes_official_jsonl(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            prediction = {
                "instance_id": "demo__repo-1",
                "model_name_or_path": "demo-model",
                "model_patch": "diff --git a/a b/a",
            }
            store = BatchStore(root, config={"model": "demo-model"})
            store.update("demo__repo-1", state="completed", status="success", prediction=prediction)
            resumed = BatchStore(root, config={"model": "ignored-on-resume"})
            self.assertTrue(resumed.completed("demo__repo-1"))
            lines = (root / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(lines[0]), prediction)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_failed_or_patchless_runs_remain_retriable_and_are_not_predictions(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            store = BatchStore(root, config={"model": "demo-model"})
            store.update(
                "demo__repo-1",
                state="failed",
                status="terminated",
                prediction={
                    "instance_id": "demo__repo-1",
                    "model_name_or_path": "demo-model",
                    "model_patch": "diff --git a/tmp.txt b/tmp.txt",
                },
            )
            store.update(
                "demo__repo-2",
                state="completed",
                status="success",
                prediction={
                    "instance_id": "demo__repo-2",
                    "model_name_or_path": "demo-model",
                    "model_patch": "",
                },
            )
            self.assertFalse(store.completed("demo__repo-1"))
            self.assertFalse(store.completed("demo__repo-2"))
            self.assertEqual((root / "predictions.jsonl").read_text(encoding="utf-8"), "")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_workspace_pool_cleans_failed_attempt_before_retry(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        target = root / "tasks" / "demo__repo-1"
        target.mkdir(parents=True)
        try:
            subprocess.run(["git", "init"], cwd=target, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "agent@example.test"], cwd=target, check=True)
            subprocess.run(["git", "config", "user.name", "Agent Test"], cwd=target, check=True)
            (target / "source.py").write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=target, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=target, check=True, capture_output=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=target, check=True, capture_output=True, text=True
            ).stdout.strip()
            cache = root / "cache"
            cache.mkdir()
            subprocess.run(
                ["git", "clone", "--mirror", str(target), str(cache / "demo__repo.git")],
                check=True,
                capture_output=True,
            )
            (target / "source.py").write_text("modified\n", encoding="utf-8")
            (target / "scratch.txt").write_text("temporary\n", encoding="utf-8")

            task = SWEbenchTask("demo__repo-1", "demo/repo", head, "Fix it")
            prepared = WorkspacePool(root).prepare(task)

            self.assertEqual((prepared / "source.py").read_text(encoding="utf-8"), "original\n")
            self.assertFalse((prepared / "scratch.txt").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

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
            self.assertEqual(len(load_tasks(task_file)), 1)
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
