from __future__ import annotations

from typing import Any

from ..environment.base import Environment
from ..schemas import ToolResult
from .base import Tool, truncated


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a UTF-8 text file inside the workspace with optional line slicing."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "Zero-based first line."},
            "limit": {"type": "integer", "description": "Maximum lines to return."},
        },
        "required": ["path"],
    }

    def __init__(self, *, output_limit: int = 30_000):
        self.output_limit = output_limit

    def execute(self, arguments: dict[str, Any], environment: Environment) -> ToolResult:
        content = environment.read_file(str(arguments["path"]))
        lines = content.splitlines()
        offset = max(0, int(arguments.get("offset", 0)))
        limit = max(1, int(arguments.get("limit", len(lines) or 1)))
        selected = lines[offset : offset + limit]
        numbered = "\n".join(f"{offset + index + 1:6d}\t{line}" for index, line in enumerate(selected))
        return ToolResult(ok=True, output=numbered or "(empty file)")


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write a complete UTF-8 file inside the workspace."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    def execute(self, arguments: dict[str, Any], environment: Environment) -> ToolResult:
        path = str(arguments["path"])
        environment.write_file(path, str(arguments["content"]))
        return ToolResult(ok=True, output=f"Wrote {path}")


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace exactly one literal occurrence in a workspace file. The call fails if "
        "old_text is absent or ambiguous."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def execute(self, arguments: dict[str, Any], environment: Environment) -> ToolResult:
        path = str(arguments["path"])
        environment.edit_file(path, str(arguments["old_text"]), str(arguments["new_text"]))
        return ToolResult(ok=True, output=f"Edited {path}")
