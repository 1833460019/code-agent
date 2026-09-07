"""Explicit feature profiles; every entry point uses the same runtime."""
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Features:
    todo: bool = False
    skills: bool = False
    memory: bool = False
    tasks: bool = False
    background: bool = False
    cron: bool = False
    subagents: bool = False
    teams: bool = False
    worktrees: bool = False
    mcp: bool = False

    @classmethod
    def profile(cls, name: str) -> "Features":
        if name == "baseline":
            return cls()
        if name == "full":
            return cls(**{f.name: True for f in fields(cls)})
        raise ValueError(f"Unknown profile: {name}")
