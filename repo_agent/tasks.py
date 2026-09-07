from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .storage import atomic_json, file_lock, identifier


class TaskStore:
    """Persistent DAG with atomic claims, dependency checks and recoverable leases."""
    def __init__(self, root: Path):
        self.path, self.lock = root / "tasks.json", root / "tasks.lock"

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def list(self) -> list[dict]:
        with file_lock(self.lock):
            return list(self._read().values())

    def get(self, task_id: str) -> dict:
        with file_lock(self.lock):
            return self._read()[identifier(task_id)]

    def create(self, subject: str, description: str = "", blocked_by: list[str] | None = None) -> dict:
        if not subject.strip():
            raise ValueError("subject is required")
        with file_lock(self.lock):
            data = self._read()
            deps = list(dict.fromkeys(blocked_by or []))
            if any(dep not in data for dep in deps):
                raise ValueError("All dependencies must exist")
            key = uuid.uuid4().hex[:12]
            task = dict(id=key, subject=subject, description=description, blocked_by=deps,
                        status="pending", owner=None, lease_until=0, result="", updated_at=time.time())
            data[key] = task
            atomic_json(self.path, data)
            return task

    def claim(self, owner: str, task_id: str | None = None, lease_seconds: float = 3600) -> dict | None:
        identifier(owner)
        with file_lock(self.lock):
            data = self._read()
            if task_id is not None and task_id not in data:
                raise KeyError(task_id)
            candidates = [data[task_id]] if task_id else list(data.values())
            for task in candidates:
                available = task["status"] == "pending" or (
                    task["status"] == "in_progress" and task["lease_until"] < time.time())
                ready = all(data[d]["status"] == "completed" for d in task["blocked_by"])
                if available and ready:
                    task.update(status="in_progress", owner=owner,
                                lease_until=time.time() + lease_seconds, updated_at=time.time())
                    atomic_json(self.path, data)
                    return task.copy()
            return None

    def update(self, task_id: str, *, owner: str, status: str, result: str = "",
               blocked_by: list[str] | None = None) -> dict:
        if status not in {"pending", "in_progress", "completed", "failed"}:
            raise ValueError("Invalid task status")
        with file_lock(self.lock):
            data = self._read()
            task = data[identifier(task_id)]
            if task["owner"] not in {None, owner}:
                raise ValueError("Task belongs to another agent")
            if blocked_by is not None:
                if any(dep not in data or dep == task_id for dep in blocked_by):
                    raise ValueError("Invalid dependency")
                task["blocked_by"] = list(dict.fromkeys(blocked_by))
                def visit(key, path):
                    if key in path:
                        raise ValueError("Dependency cycle")
                    for dep in data[key]["blocked_by"]:
                        visit(dep, path | {key})
                visit(task_id, set())
            if status in {"in_progress", "completed"} and any(
                    data[d]["status"] != "completed" for d in task["blocked_by"]):
                raise ValueError("Dependencies are incomplete")
            task.update(status=status, result=result, updated_at=time.time(),
                        owner=None if status == "pending" else owner,
                        lease_until=time.time() + 3600 if status == "in_progress" else 0)
            atomic_json(self.path, data)
            return task

    def delete(self, task_id: str) -> str:
        with file_lock(self.lock):
            data = self._read()
            task = data[identifier(task_id)]
            if task["status"] == "in_progress" or any(task_id in t["blocked_by"] for t in data.values()):
                raise ValueError("Cannot delete a running or depended-on task")
            del data[task_id]
            atomic_json(self.path, data)
        return "Deleted task"


class TodoList:
    def __init__(self):
        self.items: list[dict] = []
        self.last_update = 0

    def update(self, items: list[dict]) -> list[dict]:
        for item in items:
            if not item.get("content") or item.get("status") not in {"pending", "in_progress", "completed"}:
                raise ValueError("Each todo needs content and a valid status")
        if sum(i["status"] == "in_progress" for i in items) > 1:
            raise ValueError("Only one todo may be in progress")
        self.items = items
        return self.items
