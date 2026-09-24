"""Deterministic coding-agent metrics from persisted run artifacts.

No model calls are made here. A successful finish gate is deliberately distinct
from an externally judged SWE-bench resolution.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_run(path: Path) -> dict[str, Any]:
    """Load one completed run without copying prompts or tool output into reports."""
    result = _read_json(path / "result.json")
    if result is None:
        raise FileNotFoundError(path / "result.json")
    return {
        "result": result,
        "trajectory": _read_json(path / "trajectory.json"),
        "verification": _read_json(path / "verification.json"),
    }


def load_runs(root: Path) -> list[dict[str, Any]]:
    return [load_run(path.parent) for path in sorted(root.rglob("result.json"))]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if value >= 0 else None


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {"value": numerator / denominator if denominator else None,
            "numerator": numerator, "denominator": denominator}


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _distribution(values: list[float]) -> dict[str, int | float | None]:
    return {"count": len(values), "p50": _quantile(values, 0.5), "p95": _quantile(values, 0.95)}


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate observables; missing evidence is excluded, never counted as failure."""
    total = len(runs)
    statuses: Counter[str] = Counter()
    finish_accepted = first_pass = command_verified = patch_runs = 0
    gate_observed = recovery_observed = recovery_succeeded = 0
    command_observed = tool_observed = failed_tools = tool_calls = tool_call_runs = 0
    runtimes: list[float] = []
    tokens: list[float] = []
    verified_tokens: list[float] = []
    model_latencies: list[float] = []
    for run in runs:
        result = run["result"]
        trajectory = run["trajectory"] or {}
        verification = run["verification"] or {}
        status = str(result.get("status", "unknown"))
        statuses[status] += 1
        patch_runs += bool(str(result.get("model_patch") or "").strip())
        runtime = _number(result.get("runtime"))
        if runtime is not None:
            runtimes.append(runtime)
        used_tokens = _number(result.get("total_tokens"))
        if used_tokens is not None:
            tokens.append(used_tokens)
        counted = _number(result.get("total_tool_calls"))
        if counted is not None:
            tool_calls += int(counted)
            tool_call_runs += 1
        attempts = verification.get("attempts")
        if not isinstance(attempts, list):
            attempts = []
            last = result.get("verification")
            if isinstance(last, dict):
                attempts = [last]
        if attempts:
            gate_observed += 1
            accepted = status == "success" and any(
                isinstance(attempt, dict) and attempt.get("accepted") is True for attempt in attempts
            )
            finish_accepted += accepted
            first_pass += accepted and isinstance(attempts[0], dict) and attempts[0].get("accepted") is True
            if accepted and used_tokens is not None:
                verified_tokens.append(used_tokens)
            policy = verification.get("policy") or {}
            expected_commands = policy.get("commands") if isinstance(policy, dict) else None
            if isinstance(expected_commands, list) and expected_commands:
                command_observed += 1
                successful_attempt = next(
                    (a for a in attempts if isinstance(a, dict) and a.get("accepted") is True), None
                ) if accepted else None
                commands = successful_attempt.get("commands") if successful_attempt else None
                if (isinstance(commands, list) and len(commands) == len(expected_commands)
                        and all(isinstance(c, dict) and c.get("command") == expected
                                and c.get("exit_code") == 0 and c.get("timed_out") is not True
                                for c, expected in zip(commands, expected_commands))):
                    command_verified += 1
        if run["trajectory"] is not None:
            events = trajectory.get("events") or []
            if isinstance(events, list) and any(
                isinstance(event, dict) and event.get("type") == "recovery" for event in events
            ):
                recovery_observed += 1
                recovery_succeeded += status == "success"
            steps = trajectory.get("steps") or []
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    latency = _number(step.get("latency"))
                    if latency is not None:
                        model_latencies.append(latency)
                    calls = step.get("tool_calls") or []
                    if isinstance(calls, list):
                        for call in calls:
                            if isinstance(call, dict) and isinstance(call.get("ok"), bool):
                                tool_observed += 1
                                failed_tools += call["ok"] is False
    return {
        "runs": total,
        "status_counts": dict(sorted(statuses.items())),
        "outcomes": {
            "patch_rate": _ratio(patch_runs, total),
            "finish_gate_pass_rate": _ratio(finish_accepted, gate_observed),
            "first_pass_finish_gate_rate": _ratio(first_pass, gate_observed),
            "command_verification_pass_rate": _ratio(command_verified, command_observed),
            "recovery_success_rate": _ratio(recovery_succeeded, recovery_observed),
            "tool_error_rate": _ratio(failed_tools, tool_observed),
        },
        "efficiency": {
            "run_latency_seconds": _distribution(runtimes),
            "model_call_latency_seconds": _distribution(model_latencies),
            "total_tokens": _distribution(tokens),
            "tokens_per_finish_gate_pass": _distribution(verified_tokens),
            "tool_calls_per_run": _ratio(tool_calls, tool_call_runs),
        },
        "unavailable_without_external_labels": ["swebench_resolved_rate", "semantic_correctness"],
        "unavailable_without_instrumentation": ["prefix_cache_hit_rate", "checkpoint_integrity"],
    }
