"""AgentLoop: 核心 agent 执行循环。

- _run_loop (multi-turn): Agent's module
- _run_turn (single turn): Human's module — LLM 流式调用 + 工具结算
"""

import json
from dataclasses import dataclass
from enum import Enum

from src.agent.agent import AgentInfo
from src.agent.builtins import AgentRegistry
from src.agent.permissions import PermissionRuleset
from src.prompt.engine import SystemPromptEngine
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
    workspace: str = ""
    on_text_delta: object | None = None  # callback(text: str) 流式输出
    abort_signal: object | None = None  # asyncio.Event 中断信号


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

            needs_compact = await self.compaction.check(ctx.session_id, messages)
            if needs_compact:
                await self.compaction.execute(ctx.session_id, messages)
                continue

            system = await self.system_prompt.build(agent, ctx)

            tools = await self.tools.resolve_for_agent(agent)
            model_messages = self._to_model_messages(messages, ctx.provider_id)

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
    def _to_model_messages(messages: list, provider_id: str) -> list[dict]:
        """将 DB 消息转为 LLM API 格式。

        Anthropic: tool_use / tool_result 在同一消息的 content 数组中
        OpenAI:   tool_calls 在 assistant 消息，tool_result 为独立 role=tool 消息
        """
        if provider_id == "openai":
            return AgentLoop._to_openai_messages(messages)
        return AgentLoop._to_anthropic_messages(messages)

    @staticmethod
    def _to_anthropic_messages(messages: list) -> list[dict]:
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

    @staticmethod
    def _to_openai_messages(messages: list) -> list[dict]:
        """转为 OpenAI Chat Completions 格式。

        OpenAI 要求 tool 消息紧跟对应的 assistant(tool_calls) 之后，
        所以先输出 assistant 消息，再输出 tool_result。
        """
        result: list[dict] = []

        for msg in messages:
            parts = json.loads(msg.parts or "[]")
            if not parts:
                continue

            text_parts = [p for p in parts if p["type"] == "text"]
            tool_calls = [p for p in parts if p["type"] == "tool_call"]
            tool_results = [p for p in parts if p["type"] == "tool_result"]

            # 1. 构建主消息（user / assistant）
            content: str | None = None
            if text_parts:
                content = "\n".join(p["text"] for p in text_parts)

            tc_list = None
            if tool_calls:
                tc_list = []
                for tc in tool_calls:
                    args = tc.get("args", {})
                    if isinstance(args, dict):
                        args = json.dumps(args)
                    tc_list.append(
                        {
                            "id": tc["tool_call_id"],
                            "type": "function",
                            "function": {
                                "name": tc["tool_name"],
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

            # 2. tool_result 作为独立 role=tool 消息，紧跟 assistant 之后
            for tr in tool_results:
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr["tool_call_id"],
                        "content": tr["output"],
                    }
                )

        return result
