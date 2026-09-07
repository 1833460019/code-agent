from __future__ import annotations

import json
from pathlib import Path

from .environment.local import LocalEnvironment, process_environment
from .storage import atomic_json, file_lock, identifier


class WorktreeManager:
    def __init__(self, environment: LocalEnvironment, state_root: Path):
        self.environment = environment
        self.root = (state_root / "worktrees").resolve()
        self.path = state_root / "worktrees.json"
        self.lock = state_root / "worktrees.lock"

    def _read(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def list(self):
        with file_lock(self.lock):
            return list(self._read().values())

    def create(self, name: str, task_id: str = "") -> dict:
        identifier(name)
        with file_lock(self.lock):
            data = self._read()
            if name in data:
                raise ValueError("Worktree name already exists")
            target = self.root / name
            if target.exists():
                raise ValueError("Worktree directory already exists")
            head = self.environment.git("rev-parse", "HEAD")
            if head.returncode:
                raise ValueError("Worktrees require a committed Git repository")
            self.root.mkdir(parents=True, exist_ok=True)
            result = self.environment.git("worktree", "add", "--detach", str(target), head.stdout.strip())
            if result.returncode:
                raise RuntimeError(result.stderr)
            record = dict(name=name, path=str(target), task_id=task_id, base_commit=head.stdout.strip(), status="active")
            data[name] = record
            atomic_json(self.path, data)
            return record

    def get(self, name):
        with file_lock(self.lock):
            record = self._read()[identifier(name)]
        target = Path(record["path"]).resolve()
        if not target.is_relative_to(self.root) or target == self.root:
            raise ValueError("Invalid worktree path")
        return record

    def environment_for(self, name):
        record = self.get(name)
        env = LocalEnvironment(record["path"], command_timeout=self.environment.command_timeout)
        env.base_commit = record["base_commit"]
        return env

    def diff(self, name: str):
        return self.environment_for(name).get_diff()

    def merge(self, name: str):
        """Apply reviewed worktree changes without committing, refusing conflicts."""
        import subprocess
        patch = self.diff(name)
        if not patch:
            return "No worktree changes"
        for args in (["--check"], []):
            result = subprocess.run(["git", "apply", *args, "--whitespace=nowarn", "-"],
                                    input=patch.encode("utf-8"), capture_output=True,
                                    cwd=self.environment.workspace, timeout=30, env=process_environment())
            if result.returncode:
                raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
        return "Worktree patch applied to main workspace; run tests before finish"

    def keep(self, name: str):
        with file_lock(self.lock):
            data = self._read()
            data[identifier(name)]["status"] = "kept"
            atomic_json(self.path, data)
            return data[name]

    def remove(self, name: str):
        record = self.get(name)
        if self.environment_for(name).git("status", "--porcelain").stdout.strip():
            raise ValueError("Refusing to remove a dirty worktree; keep it or commit changes explicitly")
        result = self.environment.git("worktree", "remove", record["path"])
        if result.returncode:
            raise RuntimeError(result.stderr)
        with file_lock(self.lock):
            data = self._read()
            del data[name]
            atomic_json(self.path, data)
        return "Removed clean worktree"
