"""CompactionService: context window compaction using Head/Tail + LLM summary."""

import json

from src.context.token import estimate_tokens
from src.provider.base import ContentBlock, UnifiedMessage
from src.provider.registry import ProviderRegistry
from src.session.service import SessionService


class CompactionService:
    def __init__(self, provider_factory, sessions: SessionService):
        self._provider_factory = provider_factory
        self._sessions = sessions
        self._context_ratio = 0.8
        self._buffer_tokens = 4096

    async def check(
        self,
        session_id: str,
        messages: list,
        context_window: int | None = None,
        provider_id: str = "",
        model_id: str = "",
    ) -> bool:
        if context_window is None:
            context_window = self._resolve_context_window(provider_id, model_id)

        total_estimated = sum(
            estimate_tokens(msg.parts or "") for msg in messages
        )
        threshold = int(context_window * self._context_ratio)
        return total_estimated > threshold

    def _resolve_context_window(self, provider_id: str, model_id: str) -> int:
        """从 registry 获取模型的实际上下文窗口。找不到则回退到 200K。"""
        factory = self._provider_factory
        registry = getattr(factory, "_registry", None)
        if registry and provider_id and model_id:
            p = registry.get_provider(provider_id)
            if p:
                mi = p.resolve_model(model_id)
                if mi:
                    return mi.context
        return 200000

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
            messages=[UnifiedMessage(
                role="user",
                content=[ContentBlock(type="text", text=prompt)],
            )],
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
