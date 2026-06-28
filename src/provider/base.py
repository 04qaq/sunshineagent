"""Base ProviderClient interface, StreamEvent, and unified message types."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ContentBlock:
    """Provider-agnostic content block (a single part of a message).

    Mirrors the DB parts JSON structure. Only one payload field is non-empty
    depending on `type`.
    """

    type: str  # "text" | "tool_call" | "tool_result"
    text: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args: dict | None = None
    tool_output: str = ""
    is_error: bool = False

    @classmethod
    def from_part(cls, part: dict) -> "ContentBlock":
        t = part.get("type", "")
        return cls(
            type=t,
            text=part.get("text", ""),
            tool_call_id=part.get("tool_call_id", ""),
            tool_name=part.get("tool_name", ""),
            tool_args=part.get("args"),
            tool_output=part.get("output", ""),
            is_error=part.get("is_error", False),
        )


@dataclass
class UnifiedMessage:
    """Provider-agnostic message.

    Each provider converts this to its own wire format before sending.
    """

    role: str  # "user" | "assistant" | "system"
    content: list[ContentBlock] = field(default_factory=list)


@dataclass
class StreamEvent:
    type: str
    text: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    args: str | None = None
    finish_reason: str | None = None
    usage: dict | None = None


class ProviderClient(ABC):
    provider_id: str = ""

    @abstractmethod
    async def stream(
        self,
        model: str,
        system: str,
        messages: list[UnifiedMessage],
        tools: list[dict],
        temperature: float = 0.7,
        top_p: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        ...

    @abstractmethod
    async def generate_object(
        self,
        model: str,
        system: str,
        prompt: str,
        schema: dict,
    ) -> dict:
        ...
