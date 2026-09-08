from __future__ import annotations

from typing import Any

from ..environment.base import Environment
from ..schemas import ToolResult
from .base import Tool


class ShellTool(Tool):
    name = "shell"
    description = (
        "Run one shell command in the repository workspace. Use it to inspect files, "
        "search with rg/grep/find, inspect git, and run tests."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string", "description": "Workspace-relative directory."},
            "timeout": {"type": "number", "description": "Optional timeout in seconds."},
        },
        "required": ["command"],
    }

    def __init__(self, *, output_limit: int = 30_000):
        self.output_limit = output_limit

    def execute(self, arguments: dict[str, Any], environment: Environment) -> ToolResult:
        result = environment.execute(
            str(arguments.get("command", "")),
            cwd=str(arguments.get("cwd", ".")),
            timeout=float(arguments["timeout"]) if arguments.get("timeout") else None,
        )
        prefix = (
            f"exit_code={result.exit_code} duration={result.duration_seconds:.3f}s"
            + (" timed_out=true" if result.timed_out else "")
        )
        return ToolResult(
            ok=result.exit_code == 0,
            output=f"{prefix}\n{result.output}",
            metadata={
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "timed_out": result.timed_out,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
