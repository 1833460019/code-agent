from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_agent import DockerEnvironment, LocalEnvironment, RepoAgent, RepoAgentConfig
from repo_agent.benchmarks.swebench import SWEbenchAdapter, load_task
from repo_agent.models.factory import create_model
from scripts.common import DEFAULT_RUNS_DIR, ensure_external_workspace
from scripts.run_agent import console_event


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one JSON SWE-bench task.")
    parser.add_argument("--task-file", "--task_file", required=True)
    parser.add_argument("--instance-id", "--instance_id")
    parser.add_argument("--workspace", required=True, help="Clean checkout at task base_commit.")
    parser.add_argument("--provider", choices=["anthropic", "openai", "siliconflow"], default="anthropic")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key")
    parser.add_argument("--base-url")
    parser.add_argument("--max-steps", "--max_steps", type=int, default=50)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--command-timeout", type=float, default=120.0)
    parser.add_argument("--environment", choices=["local", "docker"], default="local")
    parser.add_argument("--docker-image")
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    return parser


async def async_main(args: argparse.Namespace) -> int:
    task = load_task(args.task_file, instance_id=args.instance_id)
    ensure_external_workspace(args.workspace)
    if args.environment == "docker":
        if not args.docker_image:
            raise ValueError("--docker-image is required for Docker execution")
        environment = DockerEnvironment(args.workspace, image=args.docker_image,
                                        command_timeout=args.command_timeout)
    else:
        environment = LocalEnvironment(args.workspace, command_timeout=args.command_timeout)
    model = create_model(
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        max_tokens=args.max_tokens,
    )
    agent = RepoAgent(
        model=model,
        environment=environment,
        config=RepoAgentConfig(max_steps=args.max_steps, runs_dir=args.runs_dir, profile="baseline"),
    )
    adapter = SWEbenchAdapter(environment)
    result = await adapter.run(task, agent, event_callback=console_event)
    prediction = adapter.prediction(result)
    prediction_path = Path(result.run_dir) / "prediction.json"
    prediction_path.write_text(json.dumps(prediction, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(prediction, ensure_ascii=False))
    return 0 if result.status != "error" else 1


def main() -> int:
    load_dotenv()
    return asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
