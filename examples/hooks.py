"""Embedding example: pass these trusted lifecycle hooks to RepoAgent(hooks=hooks)."""
from repo_agent.hooks import Hooks

hooks = Hooks()


def forbid_protected_file(payload):
    if payload["tool"] in {"write_file", "edit_file"}:
        path = payload["runtime"].environment.resolve_path(payload["arguments"]["path"])
        if path.name == "production.env":
            return False


async def record_finish(payload):
    print("Run finished:", payload["result"].termination_reason)


hooks.register("before_tool", forbid_protected_file)
hooks.register("stop", record_finish)
