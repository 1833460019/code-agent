from __future__ import annotations

from .base import Tool
from .file import EditFileTool, ReadFileTool, WriteFileTool
from .finish import FinishTool
from .shell import ShellTool


def create_coding_tools(*, output_limit: int = 30_000) -> list[Tool]:
    return [
        ShellTool(output_limit=output_limit),
        ReadFileTool(output_limit=output_limit),
        WriteFileTool(),
        EditFileTool(),
        FinishTool(),
    ]
