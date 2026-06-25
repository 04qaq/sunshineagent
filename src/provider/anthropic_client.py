"""Anthropic LLM client adapter."""

import json
from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from src.provider.base import ProviderClient, StreamEvent


class AnthropicClient(ProviderClient):
    provider_id = "anthropic"

    def __init__(self, api_key: str | None = None):
        self._client = AsyncAnthropic(api_key=api_key)

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
        async with self._client.messages.stream(
            model=model,
            system=system,
            messages=messages,
            tools=tools if tools else None,
            max_tokens=max_tokens or 16384,
            temperature=temperature,
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    yield StreamEvent(type="text_delta", text=event.text)
                elif event.type == "tool_use":
                    yield StreamEvent(
                        type="tool_call_start",
                        tool_call_id=event.id,
                        tool_name=event.name,
                        args=json.dumps(event.input),
                    )
                elif event.type == "message_stop":
                    usage = None
                    if stream.usage:
                        usage = {
                            "input_tokens": stream.usage.input_tokens,
                            "output_tokens": stream.usage.output_tokens,
                        }
                    yield StreamEvent(
                        type="finish",
                        finish_reason="stop",
                        usage=usage,
                    )

    async def generate_object(
        self,
        model: str,
        system: str,
        prompt: str,
        schema: dict,
    ) -> dict:
        from anthropic.types import ToolParam

        response = await self._client.messages.create(
            model=model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[
                ToolParam(
                    name="generate",
                    description="Generate structured output",
                    input_schema=schema,
                )
            ],
            tool_choice={"type": "tool", "name": "generate"},
            max_tokens=4096,
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        return {}
