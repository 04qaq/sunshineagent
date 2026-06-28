"""AgentLoop: 核心 agent 执行循环。

- _run_loop (multi-turn): Agent's module
- _run_turn (single turn): Human's module — LLM 流式调用 + 工具结算
"""

import asyncio
import json
from dataclasses import dataclass
from enum import Enum

from src.agent.agent import AgentInfo
from src.agent.builtins import AgentRegistry
from src.agent.permissions import PermissionRuleset
from src.prompt.engine import SystemPromptEngine
from src.provider.base import ContentBlock, ProviderClient, UnifiedMessage
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
    workspace: str = ""
    on_text_delta: object | None = None  # callback(text: str) 流式输出
    abort_signal: object | None = None  # asyncio.Event 中断信号
    ask_callback: object | None = None  # callback(questions: list) -> dict 向用户提问


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
        system_prompt_engine: SystemPromptEngine,
    ):
        self.sessions = session_service
        self.agents = agent_registry
        self.tools = tool_registry
        self.provider_factory = provider_factory
        self.compaction = compaction_service
        self.coordinator = coordinator
        self.system_prompt = system_prompt_engine

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

            needs_compact = await self.compaction.check(
                ctx.session_id, messages,
                provider_id=ctx.provider_id, model_id=ctx.model_id,
            )
            if needs_compact:
                await self.compaction.execute(ctx.session_id, messages)
                continue

            system = await self.system_prompt.build(agent, ctx)

            tools = await self.tools.resolve_for_agent(
                agent, override_permission=ctx.permission
            )
            model_messages = self._to_unified_messages(messages)

            provider = self.provider_factory.create(ctx.provider_id)

            result = await self._run_turn(
                ctx=ctx,
                provider=provider,
                system=system,
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
        system: str,
        messages: list[dict],
        tools: list[dict],
        agent: AgentInfo,
    ) -> LoopResult:
        """单轮 LLM 调用 + 工具执行。

        对应 OpenCode runner/llm.ts 的 runTurnAttempt()：
        1. 创建占位 assistant 消息
        2. 流式调用 LLM
        3. 收集 text 和 tool_calls
        4. 更新消息 parts
        5. 并发执行工具调用
        6. 将 tool_result 追加到消息
        7. 根据 finish_reason 返回 LoopResult
        """
        # 1. 创建占位 assistant 消息
        assistant_msg = await self.sessions.create_message(
            ctx.session_id, "assistant", parts=[]
        )

        # 2. 流式调用 LLM
        stream = provider.stream(
            model=ctx.model_id,
            system=system,
            messages=messages,
            tools=tools,
            temperature=agent.temperature or 0.7,
            top_p=agent.top_p,
        )

        # 3. 收集流事件
        text_buffer: list[str] = []
        tool_calls: list[ToolCall] = []
        finish_reason: str | None = None
        usage: dict | None = None

        async for event in stream:
            # 支持 Ctrl+C 中断
            if ctx.abort_signal is not None:
                abort = ctx.abort_signal
                if hasattr(abort, "is_set") and abort.is_set():
                    break

            if event.type == "text_delta" and event.text:
                text_buffer.append(event.text)
                if ctx.on_text_delta is not None:
                    cb = ctx.on_text_delta
                    if callable(cb):
                        cb(event.text)

            elif event.type == "tool_call_start":
                tool_calls.append(
                    ToolCall(
                        id=event.tool_call_id or "",
                        name=event.tool_name or "",
                        args=event.args or "{}",
                    )
                )

            elif event.type == "tool_call_delta" and event.tool_call_id:
                # 流式累积 tool call args
                for tc in tool_calls:
                    if tc.id == event.tool_call_id:
                        tc.args = event.args or tc.args
                        break

            elif event.type == "finish":
                finish_reason = event.finish_reason
                usage = event.usage

        # 中断信号触发的 stream 退出
        if finish_reason is None and ctx.abort_signal is not None:
            abort = ctx.abort_signal
            if hasattr(abort, "is_set") and abort.is_set():
                finish_reason = "stop"

        # 4. 更新 assistant 消息 parts
        parts: list[dict] = []
        text = "".join(text_buffer)
        if text:
            parts.append({"type": "text", "text": text})
        for tc in tool_calls:
            parts.append(
                {
                    "type": "tool_call",
                    "tool_call_id": tc.id,
                    "tool_name": tc.name,
                    "args": json.loads(tc.args) if tc.args else {},
                }
            )
        await self.sessions.update_message(
            assistant_msg.id,
            parts=json.dumps(parts),
            finish_reason=finish_reason,
            usage=json.dumps(usage) if usage else None,
        )

        # 5. 执行工具调用（并行）
        if tool_calls:
            tool_results = await self._settle_tools(ctx, agent, tool_calls)
            for tr in tool_results:
                await self.sessions.append_part(
                    assistant_msg.id,
                    {
                        "type": "tool_result",
                        "tool_call_id": tr["call_id"],
                        "output": tr["output"],
                        "is_error": tr["is_error"],
                    },
                )

            # 纯工具调用（无文本），继续下一轮
            if not text or finish_reason == "tool_calls":
                return LoopResult.CONTINUE

        # 6. 根据 finish_reason 决定下一步
        if finish_reason == "stop":
            return LoopResult.STOP
        elif finish_reason == "length":
            return LoopResult.COMPACT

        return LoopResult.CONTINUE

    async def _settle_tools(
        self,
        ctx: SessionContext,
        agent: AgentInfo,
        tool_calls: list,
    ) -> list[dict]:
        # 1. 预验证：按 tool_call_id 索引，校验权限和存在性
        valid: dict[str, tuple] = {}  # call_id → (tool, args)
        results: list[dict] = []

        for tc in tool_calls:
            args = {}
            if isinstance(tc.args, str) and tc.args:
                args = json.loads(tc.args)
            elif isinstance(tc.args, dict):
                args = tc.args

            tool = self.tools.get(tc.name)
            if not tool:
                results.append(
                    {"call_id": tc.id, "output": f"Unknown tool: {tc.name}", "is_error": True}
                )
                continue

            if agent.permission and not agent.permission.can_use(tc.name):
                results.append(
                    {"call_id": tc.id, "output": f"Permission denied: {tc.name}", "is_error": True}
                )
                continue

            valid[tc.id] = (tool, args, tc.name)

        # 2. 并行执行所有校验通过的工具
        if valid:
            async def _run_one(call_id, tool, args):
                try:
                    tool_ctx = ToolContext(
                        session_id=ctx.session_id,
                        agent=ctx.agent_name,
                        assistant_message_id=None,
                        tool_call_id=call_id,
                        ask_callback=ctx.ask_callback,
                    )
                    output = await tool.execute(args, tool_ctx)
                    truncated = self._truncate_output(output, max_tokens=10000)
                    return {"call_id": call_id, "output": truncated, "is_error": False}
                except Exception as e:
                    return {"call_id": call_id, "output": str(e), "is_error": True}

            parallel_results = await asyncio.gather(
                *(_run_one(cid, t, a) for cid, (t, a, _) in valid.items())
            )
            results.extend(parallel_results)

        return results

    def _truncate_output(self, result, max_tokens: int = 10000) -> str:
        output = result.output if hasattr(result, "output") else str(result)
        if len(output) > max_tokens * 4:
            output = output[: max_tokens * 4] + "\n... [output truncated]"
        return output

    @staticmethod
    def _to_unified_messages(messages: list) -> list[UnifiedMessage]:
        """将 DB 消息转为 provider-agnostic UnifiedMessage 列表。

        每个 provider client 负责将自己格式化为对应的 wire format。
        这避免了在 agent loop 中耦合 provider 特定的格式逻辑。
        """
        result: list[UnifiedMessage] = []
        for msg in messages:
            parts = json.loads(msg.parts or "[]")
            content = [ContentBlock.from_part(p) for p in parts]
            result.append(UnifiedMessage(role=msg.role, content=content))
        return result
