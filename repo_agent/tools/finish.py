from __future__ import annotations

from typing import Any

from ..environment.base import Environment
from ..schemas import ToolResult
from .base import Tool


class FinishTool(Tool):
    name = "finish"
    description = (
        "End the run after inspecting the final diff and running relevant tests. "
        "The workspace patch, not this message, is the submitted answer."
    )
    input_schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }

    def execute(self, arguments: dict[str, Any], environment: Environment) -> ToolResult:
        summary = str(arguments.get("summary", "Task complete.")).strip()
        return ToolResult(ok=True, output=summary, finished=True)
