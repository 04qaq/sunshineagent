"""Anthropic LLM client adapter.

Handles conversion from UnifiedMessage to Anthropic Messages API format
and provider-specific post-processing (类似 OpenCode 的 ProviderTransform).
"""

import json
from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from src.provider.base import ContentBlock, ProviderClient, StreamEvent, UnifiedMessage


class AnthropicClient(ProviderClient):
    provider_id = "anthropic"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url or None,
        )

    def _convert_messages(self, messages: list[UnifiedMessage]) -> list[dict]:
        """Convert UnifiedMessage list to Anthropic Messages API format.

        Anthropic uses content blocks: tool_use / tool_result / text all live
        inside the same message's content array.
        """
        result: list[dict] = []
        for msg in messages:
            content: list[dict] = []
            for block in msg.content:
                if block.type == "text":
                    content.append({"type": "text", "text": block.text})
                elif block.type == "tool_call":
                    content.append(
                        {
                            "type": "tool_use",
                            "id": block.tool_call_id,
                            "name": block.tool_name,
                            "input": block.tool_args or {},
                        }
                    )
                elif block.type == "tool_result":
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.tool_call_id,
                            "content": block.tool_output,
                            "is_error": block.is_error,
                        }
                    )
            result.append({"role": msg.role, "content": content})
        return self._postprocess(result)

    def _postprocess(self, messages: list[dict]) -> list[dict]:
        """Anthropic-specific post-processing.

        - Filter out empty text blocks (Anthropic rejects them).
        - Ensure tool_use IDs only contain [a-zA-Z0-9_-].
        """
        import re

        for msg in messages:
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            filtered: list[dict] = []
            for block in content:
                if block.get("type") == "text" and not block.get("text", "").strip():
                    continue
                if block.get("type") == "tool_use":
                    block["id"] = re.sub(
                        r"[^a-zA-Z0-9_-]", "", block.get("id", "")
                    )
                filtered.append(block)

            if not filtered:
                msg["content"] = [{"type": "text", "text": "."}]
            else:
                msg["content"] = filtered

        return messages

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
        api_messages = self._convert_messages(messages)

        async with self._client.messages.stream(
            model=model,
            system=system,
            messages=api_messages,
            tools=tools if tools else None,
            max_tokens=max_tokens or 16384,
            temperature=temperature,
        ) as stream:
            stopped = False
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
                    stopped = True
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
            if not stopped:
                yield StreamEvent(
                    type="finish",
                    finish_reason="stop",
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
