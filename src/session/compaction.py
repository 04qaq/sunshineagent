"""CompactionService: context window compaction using Head/Tail + LLM summary."""

import json

from src.context.token import estimate_tokens
from src.session.service import SessionService


class CompactionService:
    def __init__(self, provider_factory, sessions: SessionService):
        self._provider_factory = provider_factory
        self._sessions = sessions
        self._context_ratio = 0.8
        self._buffer_tokens = 4096

    async def check(
        self, session_id: str, messages: list, context_window: int | None = None
    ) -> bool:
        if context_window is None:
            context_window = 200000

        total_estimated = sum(
            estimate_tokens(msg.parts or "") for msg in messages
        )
        threshold = context_window - max(16384, self._buffer_tokens)
        return total_estimated > threshold

    async def execute(self, session_id: str, messages: list):

        keep_last = max(3, len(messages) // 4)
        head = messages[:-keep_last]

        if not head:
            return

        summary = await self._generate_summary(head)

        for msg in head:
            await self._sessions.update_message(msg.id, compacted=True)

        await self._sessions.create_message(
            session_id,
            "system",
            parts=[
                {
                    "type": "text",
                    "text": f"<conversation-checkpoint>\n{summary}\n</conversation-checkpoint>",
                }
            ],
        )

    async def _generate_summary(self, messages: list) -> str:
        history_text: list[str] = []
        for msg in messages:
            parts = json.loads(msg.parts or "[]")
            text = " ".join(p.get("text", "") for p in parts if p["type"] == "text")
            if text:
                history_text.append(f"[{msg.role}]: {text[:500]}")

        prompt = COMPACTION_PROMPT.format(history="\n".join(history_text))

        provider = self._provider_factory.create("anthropic")
        result: list[str] = []
        async for event in provider.stream(
            model="claude-haiku-4-5",
            system="You are a conversation summarizer.",
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            temperature=0.1,
        ):
            if event.type == "text_delta" and event.text:
                result.append(event.text)
        return "".join(result)


COMPACTION_PROMPT = """\
Summarize the following conversation history. Include:
- Goal: What the user is trying to accomplish
- Constraints: Any constraints mentioned
- Progress: What has been accomplished so far
- Key Decisions: Important decisions made
- Next Steps: What remains to be done
- Critical Context: Any context that must not be lost
- Relevant Files: Files that have been examined or modified

Conversation:
{history}
"""
