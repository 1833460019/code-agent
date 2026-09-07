from __future__ import annotations

import inspect
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Callable, Literal


@dataclass(frozen=True)
class PermissionRule:
    tool: str
    action: Literal["allow", "deny", "ask"]
    command: str = "*"


class PermissionPolicy:
    """A tool approval policy, not an OS sandbox. Deny rules take precedence."""

    def __init__(self, mode: str = "trusted", rules: list[PermissionRule] | None = None,
                 approver: Callable | None = None):
        if mode not in {"trusted", "ask", "readonly"}:
            raise ValueError("permission mode must be trusted, ask, or readonly")
        self.mode, self.rules, self.approver = mode, rules or [], approver

    async def check(self, name: str, arguments: dict) -> None:
        if name == "background_run":
            await self.check("shell", arguments)
        matched = [r.action for r in self.rules if fnmatchcase(name, r.tool)
                   and fnmatchcase(str(arguments.get("command", "")), r.command)]
        if "deny" in matched:
            action = "deny"
        elif "ask" in matched:
            action = "ask"
        elif "allow" in matched:
            action = "allow"
        elif self.mode == "trusted":
            action = "allow"
        elif name in {"read_file", "list_files", "grep_files", "finish", "tool_help",
                      "task_list", "task_get", "memory_search", "memory_read", "skill_list",
                      "load_skill", "background_check", "team_status", "inbox", "cron_list",
                      "worktree_list", "artifact_read"}:
            action = "allow"
        else:
            action = "deny" if self.mode == "readonly" else "ask"
        if action == "ask" and self.approver:
            approved = self.approver(name, arguments)
            if inspect.isawaitable(approved):
                approved = await approved
            if approved is True:
                return
        elif action == "allow":
            return
        raise PermissionError(f"{action}: {name} was not authorized")
