from __future__ import annotations

import inspect
from collections import defaultdict
from typing import Any, Callable


class HookRejected(RuntimeError):
    pass


class Hooks:
    """Ordered sync/async hooks. Returning False vetoes the current operation."""
    EVENTS = {"user_prompt", "before_model", "after_model", "before_tool", "after_tool", "error", "stop"}

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def register(self, event: str, handler: Callable) -> None:
        if event not in self.EVENTS:
            raise ValueError(f"Unknown hook event: {event}")
        self._handlers[event].append(handler)

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        for handler in self._handlers[event]:
            result = handler(payload)
            if inspect.isawaitable(result):
                result = await result
            if result is False:
                raise HookRejected(f"{event} rejected by {handler.__name__}")
