from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SWEbenchTask:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SWEbenchTask":
        required = ("instance_id", "repo", "base_commit", "problem_statement")
        missing = [key for key in required if not str(data.get(key, "")).strip()]
        if missing:
            raise ValueError(f"SWE-bench task is missing required fields: {', '.join(missing)}")
        return cls(**{key: str(data[key]) for key in required})


def load_task(path: str | Path, *, instance_id: str | None = None) -> SWEbenchTask:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return SWEbenchTask.from_dict(payload)
    if not isinstance(payload, list):
        raise ValueError("Task JSON must contain an object or a list of objects")
    if not instance_id:
        if len(payload) != 1:
            raise ValueError("--instance-id is required when task JSON contains multiple tasks")
        return SWEbenchTask.from_dict(payload[0])
    for item in payload:
        if isinstance(item, dict) and item.get("instance_id") == instance_id:
            return SWEbenchTask.from_dict(item)
    raise ValueError(f"Instance not found in task JSON: {instance_id}")
