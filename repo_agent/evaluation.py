from __future__ import annotations

import random
import statistics
from collections import Counter


def classify_failure(result: dict) -> str:
    if result.get("resolved") is True:
        return "resolved"
    reason = result.get("termination_reason", "")
    if result.get("status") == "success" and result.get("model_patch", "").strip():
        verification = result.get("verification") or {}
        return "candidate" if verification.get("accepted", True) else "verification_failed"
    if not result.get("model_patch", "").strip():
        return "no_patch"
    if reason in {"max_steps", "max_runtime"}:
        return "budget_exhausted"
    if reason == "cancelled":
        return "cancelled"
    if reason in {"model_error", "runtime_error"}:
        return "model_error"
    if reason in {"patch_error", "initialization_error", "cleanup_error"}:
        return "infrastructure_error"
    return "unfinished"


def summarize(results: list[dict]) -> dict:
    categories = Counter(classify_failure(result) for result in results)
    tokens = [int(result.get("total_tokens", 0)) for result in results]
    runtimes = [float(result.get("runtime", 0)) for result in results]
    return {
        "runs": len(results),
        "categories": dict(sorted(categories.items())),
        "candidate_rate": _rate(categories.get("candidate", 0), len(results)),
        "resolved_rate": _rate(categories.get("resolved", 0), len(results)),
        "patch_rate": _rate(sum(bool(r.get("model_patch", "").strip()) for r in results), len(results)),
        "mean_tokens": statistics.fmean(tokens) if tokens else 0,
        "median_runtime_seconds": statistics.median(runtimes) if runtimes else 0,
    }


def paired_bootstrap(baseline: list[dict], candidate: list[dict], *, samples: int = 5000, seed: int = 0) -> dict:
    left = {row["instance_id"]: _score(row) for row in baseline}
    right = {row["instance_id"]: _score(row) for row in candidate}
    keys = sorted(left.keys() & right.keys())
    if not keys:
        raise ValueError("No paired instance_id values")
    deltas = [right[key] - left[key] for key in keys]
    rng = random.Random(seed)
    estimates = sorted(
        sum(rng.choice(deltas) for _ in deltas) / len(deltas) for _ in range(samples)
    )
    return {
        "pairs": len(keys),
        "delta": sum(deltas) / len(deltas),
        "ci95": [estimates[int(samples * 0.025)], estimates[min(samples - 1, int(samples * 0.975))]],
        "samples": samples,
        "seed": seed,
    }


def _score(result: dict) -> int:
    if "resolved" in result:
        return int(bool(result["resolved"]))
    return int(classify_failure(result) == "candidate")


def _rate(value: int, total: int) -> float:
    return value / total if total else 0.0
