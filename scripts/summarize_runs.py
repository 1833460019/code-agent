from __future__ import annotations

import argparse
import json
from pathlib import Path

from repo_agent.evaluation import paired_bootstrap, summarize


def load_results(root: str) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path(root).rglob("result.json"))
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Agent results and paired ablations.")
    parser.add_argument("results")
    parser.add_argument("--compare")
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    args = parser.parse_args()
    primary = load_results(args.results)
    report = {"primary": summarize(primary)}
    if args.compare:
        comparison = load_results(args.compare)
        report["comparison"] = summarize(comparison)
        report["paired_bootstrap"] = paired_bootstrap(
            primary, comparison, samples=args.bootstrap_samples
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
