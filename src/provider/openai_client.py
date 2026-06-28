"""OpenAI LLM client adapter.

Handles conversion from UnifiedMessage to OpenAI Chat Completions format
and provider-specific post-processing (类似 OpenCode 的 ProviderTransform).
"""

import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from src.provider.base import ContentBlock, ProviderClient, StreamEvent, UnifiedMessage


class OpenAIClient(ProviderClient):
    provider_id = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
        )

    def _convert_messages(self, messages: list[UnifiedMessage]) -> list[dict]:
        """Convert UnifiedMessage list to OpenAI Chat Completions format.

        OpenAI requires tool messages to immediately follow the assistant message
        that contains the corresponding tool_calls.
        """
        result: list[dict] = []

        for msg in messages:
            text_parts = [b for b in msg.content if b.type == "text"]
            tool_calls = [b for b in msg.content if b.type == "tool_call"]
            tool_results = [b for b in msg.content if b.type == "tool_result"]

            # 1. Build main message (user / assistant)
            content: str | None = None
            if text_parts:
                content = "\n".join(b.text for b in text_parts)

            tc_list = None
            if tool_calls:
                tc_list = []
                for tc in tool_calls:
                    args = tc.tool_args or {}
                    if isinstance(args, dict):
                        args = json.dumps(args)
                    tc_list.append(
                        {
                            "id": tc.tool_call_id,
                            "type": "function",
                            "function": {
                                "name": tc.tool_name,
                                "arguments": args,
                            },
                        }
                    )

            msg_data: dict = {"role": msg.role}
            if content is not None:
                msg_data["content"] = content
            if tc_list:
                msg_data["tool_calls"] = tc_list
            if content is None and not tc_list:
                msg_data["content"] = ""

            result.append(msg_data)

            # 2. tool_result as independent role=tool messages, following assistant
            for tr in tool_results:
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr.tool_call_id,
                        "content": tr.tool_output,
                    }
                )

        return self._postprocess(result)

    def _postprocess(self, messages: list[dict]) -> list[dict]:
        r"""Provider-specific post-processing.

        Mirrors OpenCode's normalizeMessages() — handles quirks of
        specific providers that use OpenAI-compatible protocol.
        """
        for msg in messages:
            ctx = msg.get("content")

            # DeepSeek: 不接受 null content（某些版本）
            if ctx is None:
                msg["content"] = ""

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

        openai_tools = None
        if tools:
            if "input_schema" in tools[0]:
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
            else:
                openai_tools = [
                    {"type": "function", "function": t} for t in tools
                ]

        stream = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}] + api_messages,
            tools=openai_tools,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
        )

        tool_call_buffer: dict[int, dict] = {}
        final_finish: str | None = None
        final_usage: dict | None = None

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta and delta.content:
                yield StreamEvent(type="text_delta", text=delta.content)

            if delta and delta.tool_calls:
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

            fr = chunk.choices[0].finish_reason
            if fr is not None:
                final_finish = fr
                final_usage = chunk.usage.model_dump() if chunk.usage else None

        for buf in tool_call_buffer.values():
            if buf["id"]:
                yield StreamEvent(
                    type="tool_call_start",
                    tool_call_id=buf["id"],
                    tool_name=buf["name"],
                    args=buf["args"],
                )
        yield StreamEvent(
            type="finish",
            finish_reason=final_finish or "stop",
            usage=final_usage,
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
