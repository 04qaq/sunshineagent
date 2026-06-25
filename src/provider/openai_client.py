"""OpenAI LLM client adapter."""

import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from src.provider.base import ProviderClient, StreamEvent


class OpenAIClient(ProviderClient):
    provider_id = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

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
        openai_tools = None
        if tools:
            openai_tools = [{"type": "function", "function": t} for t in tools]
            # handle anthropic-format tools if needed
            if isinstance(tools[0], dict) and "input_schema" in tools[0]:
                openai_tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["input_schema"],
                        },
                    }
                    for t in tools
                ]

        stream = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}] + messages,
            tools=openai_tools,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
        )

        tool_call_buffer: dict[int, dict] = {}

        async for chunk in stream:
            delta = chunk.choices[0].delta

            if delta.content:
                yield StreamEvent(type="text_delta", text=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_buffer:
                        tool_call_buffer[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name if tc.function else "",
                            "args": "",
                        }
                    buf = tool_call_buffer[idx]
                    if tc.id:
                        buf["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            buf["name"] = tc.function.name
                        if tc.function.arguments:
                            buf["args"] += tc.function.arguments

            if chunk.choices[0].finish_reason:
                for buf in tool_call_buffer.values():
                    yield StreamEvent(
                        type="tool_call_start",
                        tool_call_id=buf["id"],
                        tool_name=buf["name"],
                        args=buf["args"],
                    )
                yield StreamEvent(
                    type="finish",
                    finish_reason=chunk.choices[0].finish_reason,
                    usage=chunk.usage.model_dump() if chunk.usage else None,
                )

    async def generate_object(
        self,
        model: str,
        system: str,
        prompt: str,
        schema: dict,
    ) -> dict:
        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema},
            },
            temperature=0.1,
        )
        return json.loads(response.choices[0].message.content or "{}")
