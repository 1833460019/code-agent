from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_agent import LocalEnvironment, RepoAgent, RepoAgentConfig, VerificationPolicy
from repo_agent.benchmarks.swebench import BatchStore, SWEbenchAdapter, WorkspacePool, load_tasks
from repo_agent.models.factory import create_model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a resumable SWE-bench task batch.")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--provider", choices=["anthropic", "openai", "siliconflow"], default="anthropic")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key")
    parser.add_argument("--base-url")
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--max-runtime", type=float, default=1800)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--verify-command", action="append", default=[])
    return parser


async def async_main(args: argparse.Namespace) -> int:
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        raise ValueError("--workers and --limit must be positive")
    tasks = load_tasks(args.task_file)
    if args.instance_id:
        selected = set(args.instance_id)
        tasks = [task for task in tasks if task.instance_id in selected]
        missing = selected - {task.instance_id for task in tasks}
        if missing:
            raise ValueError("Unknown instances: " + ", ".join(sorted(missing)))
    if args.limit:
        tasks = tasks[: args.limit]
    config = {
        "task_file": str(Path(args.task_file).resolve()),
        "provider": args.provider,
        "model": args.model,
        "max_steps": args.max_steps,
        "max_tokens": args.max_tokens,
        "verify_commands": args.verify_command,
    }
    store = BatchStore(args.output_dir, config=config)
    pool = WorkspacePool(args.workspace_root)
    semaphore = asyncio.Semaphore(args.workers)

    async def run_one(task) -> None:
        if store.completed(task.instance_id):
            print(f"[skip] {task.instance_id}")
            return
        async with semaphore:
            store.update(task.instance_id, state="preparing", repo=task.repo, base_commit=task.base_commit)
            print(f"[prepare] {task.instance_id}")
            try:
                workspace = await asyncio.to_thread(pool.prepare, task)
                store.update(task.instance_id, state="running", workspace=str(workspace))
                model = create_model(
                    provider=args.provider,
                    model=args.model,
                    api_key=args.api_key,
                    base_url=args.base_url,
                    max_tokens=args.max_tokens,
                )
                environment = LocalEnvironment(workspace)
                agent = RepoAgent(
                    model=model,
                    environment=environment,
                    config=RepoAgentConfig(
                        max_steps=args.max_steps,
                        max_runtime=args.max_runtime,
                        profile="baseline",
                        permission_mode="trusted",
                        runs_dir=Path(args.output_dir) / "runs",
                        state_dir=Path(args.output_dir) / "state" / task.instance_id,
                        verification=VerificationPolicy(
                            require_patch=True, commands=args.verify_command
                        ),
                    ),
                )
                adapter = SWEbenchAdapter(environment)
                result = await adapter.run(task, agent)
                prediction = adapter.prediction(result)
                succeeded = result.status == "success" and bool(result.model_patch.strip())
                store.update(
                    task.instance_id,
                    state="completed" if succeeded else "failed",
                    status=result.status,
                    termination_reason=result.termination_reason,
                    total_tokens=result.total_tokens,
                    runtime=result.runtime,
                    run_dir=result.run_dir,
                    prediction=prediction,
                )
                print(f"[done] {task.instance_id}: {result.status}, tokens={result.total_tokens}")
            except Exception as exc:
                store.update(
                    task.instance_id,
                    state="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
                print(f"[error] {task.instance_id}: {type(exc).__name__}: {exc}")

    await asyncio.gather(*(run_one(task) for task in tasks))
    failures = sum(
        store.data["tasks"].get(task.instance_id, {}).get("state") in {"error", "failed"}
        for task in tasks
    )
    print(json.dumps({"selected": len(tasks), "errors": failures}, ensure_ascii=False))
    return 1 if failures else 0


def main() -> int:
    load_dotenv()
    return asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
