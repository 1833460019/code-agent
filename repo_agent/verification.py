from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class VerificationPolicy:
    """Evidence that must exist before the finish tool can end a run."""

    require_patch: bool = False
    commands: list[str] = field(default_factory=list)
    command_timeout: float = 300
    require_todos_complete: bool = False

    def validate(self) -> None:
        if self.command_timeout <= 0:
            raise ValueError("verification command_timeout must be positive")
        if any(not command.strip() for command in self.commands):
            raise ValueError("verification commands must not be empty")
