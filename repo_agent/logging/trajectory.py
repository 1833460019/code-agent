from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class TrajectoryRecorder:
    """Incrementally persist complete model/tool interactions and final artifacts."""

    def __init__(
        self,
        *,
        runs_dir: str | Path,
        instance_id: str,
        model_name: str,
        system_prompt: str,
        problem_statement: str,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        config: dict | None = None,
    ):
        root = Path(runs_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", instance_id).strip("._") or "run"
        candidate = root / safe_id
        while True:
            try:
                candidate.mkdir()
                break
            except FileExistsError:
                candidate = root / f"{safe_id}-{uuid.uuid4().hex[:12]}"
        self.run_dir = candidate
        self.event_callback = event_callback
        self.aux_input_tokens = 0
        self.aux_output_tokens = 0
        self.data: dict[str, Any] = {
            "instance_id": instance_id,
            "model_name_or_path": model_name,
            "system_prompt": system_prompt,
            "problem_statement": problem_statement,
            "started_at": _now(),
            "steps": [],
            "events": [],
            "final_status": "running",
            "config": config or {},
        }
        self._flush_trajectory()

    def event(self, event_type: str, **payload: Any) -> None:
        event = {"timestamp": _now(), "type": event_type, **payload}
        self.data["events"].append(event)
        with (self.run_dir / "run.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self._flush_trajectory()
        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception as exc:
                self.event_callback = None
                self.event("callback_error", error=f"{type(exc).__name__}: {exc}")

    def auxiliary(self, purpose, response):
        self.aux_input_tokens += response.usage.input_tokens
        self.aux_output_tokens += response.usage.output_tokens
        self.event("auxiliary_model", purpose=purpose, response=asdict(response))

    def observation(self, output: str, limit: int) -> tuple[str, str | None]:
        if len(output) <= limit:
            return output, None
        artifact_id = uuid.uuid4().hex
        directory = self.run_dir / "outputs"
        directory.mkdir(exist_ok=True)
        (directory / f"{artifact_id}.txt").write_text(output, encoding="utf-8", newline="")
        head = max(0, limit // 2 - 100)
        return (output[:head] + f"\n[Full output: artifact_read artifact_id={artifact_id}]\n" + (output[-head:] if head else ""), artifact_id)

    def add_step(self, step: dict[str, Any]) -> None:
        self.data["steps"].append(step)
        self._flush_trajectory()

    def finalize(self, *, result: dict[str, Any], patch: str) -> None:
        self.data["final_status"] = result["status"]
        self.data["finished_at"] = _now()
        self.data["metrics"] = {
            key: result[key]
            for key in (
                "total_steps",
                "total_tool_calls",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "runtime",
            )
        }
        self.data["termination_reason"] = result["termination_reason"]
        self._flush_trajectory()
        _write_json(self.run_dir / "result.json", result)
        (self.run_dir / "patch.diff").write_text(patch, encoding="utf-8", newline="")

    def _flush_trajectory(self) -> None:
        _write_json(self.run_dir / "trajectory.json", self.data)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
