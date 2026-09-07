from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..schemas import CommandResult


class EnvironmentError(RuntimeError):
    """Raised when an environment operation is invalid or cannot be completed."""


class Environment(ABC):
    """Execution boundary used by tools and replaceable by a Docker backend."""

    @property
    @abstractmethod
    def workspace(self) -> Path:
        raise NotImplementedError

    @abstractmethod
    def execute(
        self,
        command: str,
        *,
        cwd: str | Path = ".",
        timeout: float | None = None,
    ) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    def read_file(self, path: str | Path) -> str:
        raise NotImplementedError

    @abstractmethod
    def write_file(self, path: str | Path, content: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def edit_file(self, path: str | Path, old_text: str, new_text: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_diff(self) -> str:
        raise NotImplementedError
