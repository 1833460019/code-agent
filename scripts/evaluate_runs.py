from __future__ import annotations

import argparse
import json
from pathlib import Path

from repo_agent.run_metrics import load_runs, summarize_runs


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate persisted Coding Agent runs without model calls.")
    parser.add_argument("runs", type=Path, help="Directory containing result.json run artifacts")
    parser.add_argument("--output", type=Path, help="Optional aggregate JSON output")
    args = parser.parse_args()
    if not args.runs.is_dir():
        parser.error(f"Run directory does not exist: {args.runs}")
    report = summarize_runs(load_runs(args.runs))
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
