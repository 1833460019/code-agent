from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_agent import (
    DockerEnvironment,
    LocalEnvironment,
    RepoAgent,
    RepoAgentConfig,
    VerificationPolicy,
)
from repo_agent.models.factory import create_model
from scripts.common import DEFAULT_RUNS_DIR, ensure_external_workspace
from repo_agent.permissions import PermissionPolicy, PermissionRule
from repo_agent.scheduler import CronScheduler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full or baseline repository coding agent.")
    parser.add_argument("--workspace", required=True, help="Existing Git repository checkout.")
    parser.add_argument("--task", required=True, help="Repository-level issue to solve.")
    parser.add_argument("--instance-id", "--instance_id", default="manual")
    parser.add_argument("--provider", choices=["anthropic", "openai", "siliconflow"], default="anthropic")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key")
    parser.add_argument("--base-url")
    parser.add_argument("--max-steps", "--max_steps", type=int, default=50)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--command-timeout", type=float, default=120.0)
    parser.add_argument("--environment", choices=["local", "docker"], default="local")
    parser.add_argument("--docker-image", help="Trusted POSIX image used when --environment=docker")
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    parser.add_argument("--profile", choices=["full", "baseline"], default="full")
    parser.add_argument("--state-dir")
    parser.add_argument("--permission-mode", choices=["ask", "trusted", "readonly"], default="ask")
    parser.add_argument("--permission-rules", help="JSON array of tool/command/action rules")
    parser.add_argument("--mcp-config", help="Trusted JSON file with mcpServers stdio configurations")
    parser.add_argument("--skill-root", action="append", default=[])
    parser.add_argument("--auto-memory", action="store_true", help="Extract durable facts with an extra model call")
    parser.add_argument("--fallback-model")
    parser.add_argument("--max-runtime", type=float, default=1800)
    parser.add_argument("--model-timeout", type=float, default=120)
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--child-max-steps", type=int, default=20)
    parser.add_argument("--serve", action="store_true", help="Keep consuming scheduled prompts after the initial task")
    parser.add_argument("--require-patch", action="store_true", help="Reject finish when the diff is empty")
    parser.add_argument("--require-todos-complete", action="store_true")
    parser.add_argument(
        "--verify-command",
        action="append",
        default=[],
        help="Command that must pass before finish; repeatable",
    )
    parser.add_argument("--verification-timeout", type=float, default=300)
    parser.add_argument("--resume", action="store_true", help="Resume this instance from its latest checkpoint")
    return parser


def console_event(event: dict) -> None:
    if event["type"] == "tool_start":
        print(f"[{event['step']:02d}] -> {event['tool_name']} {json.dumps(event['arguments'], ensure_ascii=False)}")
    elif event["type"] == "tool_result":
        marker = "ok" if event["ok"] else "error"
        print(f"[{event['step']:02d}] <- {event['tool_name']} ({marker})\n{event['observation']}")
    elif event["type"] == "assistant" and event.get("content"):
        print(f"[{event['step']:02d}] assistant: {event['content']}")


async def async_main(args: argparse.Namespace) -> int:
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
    async def approve(name, arguments):
        prompt = f"Allow {name} {json.dumps(arguments, ensure_ascii=False)}? [y/N] "
        if not sys.stdin.isatty():
            return False
        return (await asyncio.to_thread(input, prompt)).strip().lower() in {"y", "yes"}
    rules = [PermissionRule(**r) for r in json.loads(Path(args.permission_rules).read_text(encoding="utf-8"))] if args.permission_rules else []
    policy = PermissionPolicy(args.permission_mode, rules=rules, approver=approve)
    fallback = create_model(provider=args.provider, model=args.fallback_model, api_key=args.api_key,
                            base_url=args.base_url, max_tokens=args.max_tokens) if args.fallback_model else None
    agent = RepoAgent(
        model=model,
        environment=environment,
        policy=policy, fallback_model=fallback,
        config=RepoAgentConfig(max_steps=args.max_steps, runs_dir=args.runs_dir, profile=args.profile,
                               state_dir=args.state_dir, permission_mode=args.permission_mode,
                               mcp_config=json.loads(Path(args.mcp_config).read_text(encoding="utf-8")) if args.mcp_config else {},
                               skill_roots=args.skill_root, auto_memory=args.auto_memory,
                               max_runtime=args.max_runtime, model_timeout=args.model_timeout,
                               max_workers=args.max_workers, child_max_steps=args.child_max_steps,
                               verification=VerificationPolicy(
                                   require_patch=args.require_patch,
                                   commands=args.verify_command,
                                   command_timeout=args.verification_timeout,
                                   require_todos_complete=args.require_todos_complete,
                               ), resume=args.resume),
    )
    result = await agent.run(args.task, instance_id=args.instance_id, event_callback=console_event)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    if args.serve:
        scheduler = CronScheduler(agent.state_root)
        stop = asyncio.Event()
        async def consume(entry):
            run = await agent.run(entry["prompt"], instance_id="cron-" + entry["id"], event_callback=console_event)
            return run.to_dict()
        print("Scheduler active (UTC). Ctrl+C stops the consumer; durable jobs remain saved.")
        await scheduler.serve(consume, stop)
    return 0 if result.status != "error" else 1


def main() -> int:
    load_dotenv()
    return asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
