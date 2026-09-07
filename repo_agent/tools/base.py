from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from typing import Any

from ..environment.base import Environment
from ..schemas import ToolResult


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @abstractmethod
    def execute(self, arguments: dict[str, Any], environment: Environment) -> ToolResult:
        raise NotImplementedError

    async def aexecute(self, arguments: dict[str, Any], environment: Environment) -> ToolResult:
        worker = asyncio.create_task(asyncio.to_thread(self.execute, arguments, environment))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            if hasattr(environment, "cancel_all"):
                environment.cancel_all()
            await asyncio.gather(worker, return_exceptions=True)
            raise


class FunctionTool(Tool):
    """Small adapter for runtime services; async handlers stay on the event loop."""
    def __init__(self, name, description, properties, required, handler):
        self.name, self.description, self.handler = name, description, handler
        self.input_schema = {"type": "object", "properties": properties, "required": required,
                             "additionalProperties": False}

    def execute(self, arguments, environment):
        raise RuntimeError("Use aexecute for runtime tools")

    async def aexecute(self, arguments, environment):
        import inspect
        import json
        result = self.handler(**arguments)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, ToolResult) else ToolResult(
            ok=True, output=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))


def truncated(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    kept = max(0, limit - 80)
    return text[:kept] + f"\n... [truncated {len(text) - kept} characters]"
