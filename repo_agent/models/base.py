from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import Message, ModelResponse


class Model(ABC):
    """Provider-neutral interface consumed by the agent loop."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> ModelResponse:
        raise NotImplementedError
