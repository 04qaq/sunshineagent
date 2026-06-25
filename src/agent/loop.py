"""AgentLoop: core agent execution loop.

- _run_loop (multi-turn): Agent's module
- _run_turn (single turn): Human's module stub
"""

import json
from dataclasses import dataclass
from enum import Enum

from src.agent.agent import AgentInfo
from src.agent.builtins import AgentRegistry
from src.agent.permissions import PermissionRuleset
from src.provider.base import ProviderClient
from src.provider.factory import ProviderFactory
from src.session.compaction import CompactionService
from src.session.coordinator import RunCoordinator
from src.session.service import SessionService
from src.tool.base import ToolContext, ToolRegistry


class LoopResult(Enum):
    STOP = "stop"
    COMPACT = "compact"
    CONTINUE = "continue"


@dataclass
class SessionContext:
    session_id: str
    agent_name: str
    provider_id: str
    model_id: str
    max_steps: int | None
    permission: PermissionRuleset


@dataclass
class ToolCall:
    id: str
    name: str
    args: str


class AgentLoop:
    def __init__(
        self,
        session_service: SessionService,
        agent_registry: AgentRegistry,
        tool_registry: ToolRegistry,
        provider_factory: ProviderFactory,
        compaction_service: CompactionService,
        coordinator: RunCoordinator,
    ):
        self.sessions = session_service
        self.agents = agent_registry
        self.tools = tool_registry
        self.provider_factory = provider_factory
        self.compaction = compaction_service
        self.coordinator = coordinator

    async def run(self, ctx: SessionContext) -> str | None:
        return await self.coordinator.run_exclusive(
            ctx.session_id, self._run_loop(ctx)
        )

    async def _run_loop(self, ctx: SessionContext) -> str | None:
        step = 0
        agent = self.agents.get(ctx.agent_name)
        if agent is None:
            raise ValueError(f"Agent not found: {ctx.agent_name}")

        max_steps = ctx.max_steps or agent.max_steps

        while True:
            messages = await self.sessions.get_messages(ctx.session_id)

            last_msg = messages[-1] if messages else None
            if last_msg and last_msg.role == "assistant":
                finish = last_msg.finish_reason
                if finish == "stop":
                    return last_msg.id

            step += 1
            if max_steps and step > max_steps:
                break

            needs_compact = await self.compaction.check(ctx.session_id, messages)
            if needs_compact:
                await self.compaction.execute(ctx.session_id, messages)
                continue

            tools = await self.tools.resolve_for_agent(agent)
            model_messages = self._to_model_messages(messages)

            provider = self.provider_factory.create(ctx.provider_id)

            result = await self._run_turn(
                ctx=ctx,
                provider=provider,
                messages=model_messages,
                tools=tools,
                agent=agent,
            )

            if result == LoopResult.STOP:
                break
            elif result == LoopResult.COMPACT:
                await self.compaction.execute(ctx.session_id, messages)

        messages = await self.sessions.get_messages(ctx.session_id)
        return messages[-1].id if messages else None

    async def _run_turn(
        self,
        ctx: SessionContext,
        provider: ProviderClient,
        messages: list[dict],
        tools: list[dict],
        agent: AgentInfo,
    ) -> LoopResult:
        """Single-turn LLM call + tool execution.

        Ownership: Human module. This is a stub until the human implements it.
        """
        raise NotImplementedError("Human module stub: _run_turn()")

    async def _settle_tools(
        self,
        ctx: SessionContext,
        agent: AgentInfo,
        tool_calls: list,
    ) -> list:
        results = []
        for tc in tool_calls:
            try:
                args = {}
                if isinstance(tc.args, str) and tc.args:
                    args = json.loads(tc.args)
                elif isinstance(tc.args, dict):
                    args = tc.args

                tool = self.tools.get(tc.name)
                if not tool:
                    results.append(
                        {
                            "call_id": tc.id,
                            "output": f"Unknown tool: {tc.name}",
                            "is_error": True,
                        }
                    )
                    continue

                if agent.permission and not agent.permission.can_use(tc.name):
                    results.append(
                        {
                            "call_id": tc.id,
                            "output": f"Permission denied: {tc.name}",
                            "is_error": True,
                        }
                    )
                    continue

                tool_ctx = ToolContext(
                    session_id=ctx.session_id,
                    agent=ctx.agent_name,
                    assistant_message_id=None,
                    tool_call_id=tc.id,
                )

                output = await tool.execute(args, tool_ctx)
                truncated = self._truncate_output(output, max_tokens=10000)
                results.append(
                    {
                        "call_id": tc.id,
                        "output": truncated,
                        "is_error": False,
                    }
                )

            except Exception as e:
                results.append(
                    {
                        "call_id": tc.id,
                        "output": str(e),
                        "is_error": True,
                    }
                )
        return results

    def _truncate_output(self, result, max_tokens: int = 10000) -> str:
        output = result.output if hasattr(result, "output") else str(result)
        if len(output) > max_tokens * 4:
            output = output[: max_tokens * 4] + "\n... [output truncated]"
        return output

    @staticmethod
    def _to_model_messages(messages: list) -> list[dict]:
        result: list[dict] = []
        for msg in messages:
            parts = json.loads(msg.parts or "[]")
            content: list[dict] = []

            for p in parts:
                if p["type"] == "text":
                    content.append({"type": "text", "text": p["text"]})
                elif p["type"] == "tool_call":
                    content.append(
                        {
                            "type": "tool_use",
                            "id": p["tool_call_id"],
                            "name": p["tool_name"],
                            "input": p["args"],
                        }
                    )
                elif p["type"] == "tool_result":
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": p["tool_call_id"],
                            "content": p["output"],
                            "is_error": p.get("is_error", False),
                        }
                    )

            result.append({"role": msg.role, "content": content})
        return result
