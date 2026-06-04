from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True)
class LLMResponse:
    content: str
    raw: dict


class LLMClient(Protocol):
    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """Return a model completion for chat-style messages."""
