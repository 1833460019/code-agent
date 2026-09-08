from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a SWE-bench split from Hugging Face.")
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Lite")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--output", default="swebench-lite-dev.json")
    args = parser.parse_args()
    query = urllib.parse.urlencode(
        {
            "dataset": args.dataset,
            "config": "default",
            "split": args.split,
            "offset": 0,
            "length": 100,
        }
    )
    request = urllib.request.Request(
        "https://datasets-server.huggingface.co/rows?" + query,
        headers={"User-Agent": "lcc-repo-agent/0.2"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    tasks = []
    for entry in payload.get("rows", []):
        row = entry.get("row", {})
        tasks.append(
            {
                "instance_id": row["instance_id"],
                "repo": row["repo"],
                "base_commit": row["base_commit"],
                "problem_statement": row["problem_statement"],
            }
        )
    if not tasks:
        raise RuntimeError("The dataset server returned no tasks")
    output = Path(args.output).expanduser().resolve()
    output.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(tasks)} tasks to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
