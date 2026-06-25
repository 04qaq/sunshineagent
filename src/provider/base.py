"""Base ProviderClient interface and StreamEvent."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


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
        messages: list[dict],
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
