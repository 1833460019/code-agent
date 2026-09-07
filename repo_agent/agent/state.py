from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import Message


@dataclass(slots=True)
class AgentState:
    messages: list[Message] = field(default_factory=list)
    step: int = 0
    total_tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
