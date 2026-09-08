from __future__ import annotations

import json
import re
import subprocess
import threading
from pathlib import Path

from ...storage import atomic_json
from .schema import SWEbenchTask


class WorkspacePool:
    """Prepare one clean, isolated checkout per benchmark instance."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.cache = self.root / "cache"
        self.tasks = self.root / "tasks"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.tasks.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def prepare(self, task: SWEbenchTask) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", task.repo):
            raise ValueError(f"Invalid GitHub repository name: {task.repo}")
        key = task.repo.replace("/", "__")
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            mirror = self.cache / f"{key}.git"
            if not mirror.exists():
                self._git(self.cache, "clone", "--mirror", f"https://github.com/{task.repo}.git", mirror.name)
            target = self.tasks / _safe_name(task.instance_id)
            if not target.exists():
                self._git(self.tasks, "clone", "--no-checkout", str(mirror), target.name)
                self._git(target, "checkout", "--detach", task.base_commit)
            head = self._git(target, "rev-parse", "HEAD").stdout.strip()
            expected = self._git(target, "rev-parse", task.base_commit).stdout.strip()
            dirty = self._git(target, "status", "--porcelain").stdout.strip()
            if head != expected or dirty:
                raise RuntimeError(
                    f"Existing workspace is not clean at base_commit: {target}. "
                    "Move it aside or use a new --workspace-root."
                )
            return target

    @staticmethod
    def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=600
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])
        return result


class BatchStore:
    """Crash-safe manifest and official predictions JSONL writer."""

    def __init__(self, output_dir: str | Path, *, config: dict):
        self.root = Path(output_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "manifest.json"
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {"config": config, "tasks": {}}
            self.flush()
        self.lock = threading.Lock()

    def completed(self, instance_id: str) -> bool:
        return self.data["tasks"].get(instance_id, {}).get("state") == "completed"

    def update(self, instance_id: str, **values) -> None:
        with self.lock:
            current = self.data["tasks"].setdefault(instance_id, {})
            current.update(values)
            self.flush()

    def flush(self) -> None:
        atomic_json(self.path, self.data)
        predictions = [
            value["prediction"]
            for value in self.data["tasks"].values()
            if value.get("state") == "completed" and value.get("prediction")
        ]
        temporary = self.root / "predictions.jsonl.tmp"
        temporary.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in predictions),
            encoding="utf-8",
        )
        temporary.replace(self.root / "predictions.jsonl")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "task"
