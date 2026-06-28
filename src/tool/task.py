"""Task tool: create subagent tasks with intelligent model routing.

基于 Claude Code 的 MultiAgent 架构：
- 子 agent 用同一个 AgentLoop，差异全在隔离的 SessionContext
- Worker Context 精简 — 不传父历史，只给任务描述 + system prompt
- 子→父通信：<task-result> XML 注入，单向通道
- 防递归：子 agent 工具箱不含 task/question
- 支持 5 种 worker 类型：general, explore, code, test, document
- 支持 Executive 模式：自动规划 + 并行执行 + Reflection 重试
"""

import asyncio
import json
import time

from src.context.worker_context import TaskSpec, WorkerContextBuilder
from src.provider.registry import ProviderRegistry
from src.provider.router import ModelRouter
from src.tool.base import Tool, ToolContext, ToolResult

MAX_RETRIES = 3


class TaskTool(Tool):
    name = "task"
    description = "Launch a new agent to handle complex, multi-step tasks autonomously."
    parameters = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "prompt": {"type": "string"},
            "subagent_type": {
                "type": "string",
                "enum": ["general", "explore", "code", "test", "document"],
                "description": (
                    "Type of worker agent: "
                    "general - full tools for complex tasks; "
                    "explore - read-only code search; "
                    "code - focused on writing code; "
                    "test - focused on writing/running tests; "
                    "document - read-only + write docs"
                ),
            },
            "model": {
                "type": "string",
                "description": "Optional model override (e.g. 'deepseek/deepseek-v4-flash')",
            },
            "relevant_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of relevant file paths for context",
            },
            "run_in_background": {"type": "boolean", "default": False},
            "executive": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Enable Executive mode for complex multi-step tasks. "
                    "When enabled, the system will: "
                    "1) Generate a task dependency graph "
                    "2) Execute subtasks in parallel "
                    "3) Retry failed tasks with reflection"
                ),
            },
        },
        "required": ["description", "prompt", "subagent_type"],
    }

    def __init__(
        self,
        sessions,
        agents,
        loop_factory,
        background_jobs,
        router: ModelRouter,
        registry: ProviderRegistry,
    ):
        self._sessions = sessions
        self._agents = agents
        self._loop_factory = loop_factory
        self._jobs = background_jobs
        self._router = router
        self._registry = registry
        self._context_builder = WorkerContextBuilder()

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        # Executive 模式
        if params.get("executive"):
            return await self._execute_executive(params, ctx)

        # 普通模式
        return await self._execute_normal(params, ctx)

    async def _execute_executive(self, params: dict, ctx: ToolContext) -> ToolResult:
        """执行 Executive 模式。"""
        from src.agent.executive import ExecutiveController

        # 获取 LLM 提供者
        parent_session = await self._sessions.get(ctx.session_id)
        provider_id = parent_session.provider_id if parent_session else ""
        model_id = parent_session.model_id if parent_session else ""

        provider = self._registry.get_provider(provider_id) if provider_id else None
        if not provider:
            # 使用默认 provider
            provider_id = self._registry.default_provider
            provider = self._registry.get_provider(provider_id)

        if not provider:
            return ToolResult(output="No provider available for Executive mode.")

        # 创建 LLM 调用函数
        async def llm_caller(prompt: str) -> str:
            from src.provider.base import ContentBlock, UnifiedMessage

            messages = [
                UnifiedMessage(
                    role="user",
                    content=[ContentBlock(type="text", text=prompt)],
                )
            ]
            result = []
            async for event in provider.stream(
                model=model_id or "claude-sonnet-4-6",
                system="You are a task planning expert.",
                messages=messages,
                tools=[],
                temperature=0.3,
            ):
                if event.type == "text_delta" and event.text:
                    result.append(event.text)
            return "".join(result)

        # 创建进度回调
        def progress_callback(text: str):
            # 这里可以通过某种方式将进度发送给用户
            # 目前先打印到日志
            pass

        # 创建 Executive Controller
        controller = ExecutiveController(
            task_executor=self._execute_normal_for_executive,
            llm_caller=llm_caller,
            progress_callback=progress_callback,
        )

        # 执行
        report = await controller.execute(
            request=params["prompt"],
            session_id=ctx.session_id,
        )

        return ToolResult(output=report)

    async def _execute_normal_for_executive(
        self,
        params: dict,
        session_id: str,
    ) -> ToolResult:
        """为 Executive 模式执行单个子任务。"""
        from src.tool.base import ToolContext

        ctx = ToolContext(
            session_id=session_id,
            agent="executive",
            assistant_message_id=None,
            tool_call_id="executive",
        )
        return await self._execute_normal(params, ctx)

    async def _execute_normal(self, params: dict, ctx: ToolContext) -> ToolResult:
        """执行普通模式。"""
        subagent_type = params["subagent_type"]
        agent = self._agents.get(subagent_type)
        if not agent:
            return ToolResult(output=f"Unknown subagent type: {subagent_type}")

        parent_provider_id = ""
        parent_model_id = ""
        parent_session = await self._sessions.get(ctx.session_id)
        if parent_session:
            parent_provider_id = parent_session.provider_id or ""
            parent_model_id = parent_session.model_id or ""

        provider_id, model_id = self._resolve_model(
            params, subagent_type, parent_provider_id, parent_model_id
        )
        if not provider_id:
            return ToolResult(
                output=self._build_no_model_error(
                    subagent_type, parent_provider_id, parent_model_id
                )
            )

        for attempt in range(1, MAX_RETRIES + 1):
            start_time = time.time()
            child = await self._sessions.create(
                parent_id=ctx.session_id,
                agent=subagent_type,
                title=params["description"],
                provider_id=provider_id,
                model_id=model_id,
            )

            worker_prompt = self._build_worker_context(params, agent)

            await self._sessions.create_message(
                child.id,
                "user",
                parts=[{"type": "text", "text": worker_prompt}],
            )

            from src.agent.loop import SessionContext
            from src.agent.permissions import PermissionRuleset

            run_ctx = SessionContext(
                session_id=child.id,
                agent_name=subagent_type,
                provider_id=provider_id,
                model_id=model_id,
                max_steps=agent.max_steps,
                permission=PermissionRuleset.subagent(),
                abort_signal=ctx.abort_signal,  # 父 Ctrl+C → 子自动取消
            )

            try:
                if params.get("run_in_background"):

                    async def _run_worker(ctx=run_ctx):
                        loop = self._loop_factory()
                        return await loop.run(ctx)

                    await self._jobs.start(child.id, _run_worker())
                    return ToolResult(
                        task_id=child.id,
                        output=(
                            f"<task-result>\n"
                            f"  <agent>{subagent_type}</agent>\n"
                            f"  <status>running</status>\n"
                            f"  <session-id>{child.id}</session-id>\n"
                            f"  <summary>Task started in background</summary>\n"
                            f"</task-result>"
                        ),
                    )

                loop = self._loop_factory()
                await loop.run(run_ctx)
                elapsed = int((time.time() - start_time) * 1000)

                messages = await self._sessions.get_messages(child.id)
                usage = self._compute_usage(messages)
                text = self._extract_text(messages)

                return ToolResult(
                    output=self._build_result_xml(
                        agent=subagent_type,
                        status="completed",
                        result=text or "Task completed with no output.",
                        usage=usage,
                        elapsed_ms=elapsed,
                    )
                )

            except asyncio.CancelledError:
                elapsed = int((time.time() - start_time) * 1000)
                return ToolResult(
                    output=self._build_result_xml(
                        agent=subagent_type,
                        status="stopped",
                        result="Task cancelled by user.",
                        elapsed_ms=elapsed,
                    )
                )

            except Exception as e:
                error_msg = str(e)[:300]
                elapsed = int((time.time() - start_time) * 1000)
                self._router.mark_unavailable(provider_id, model_id)

                if attempt < MAX_RETRIES:
                    fallback = self._router.select(
                        subagent_type,
                        self._registry,
                        parent_provider_id,
                        parent_model_id,
                    )
                    if fallback:
                        provider_id, model_id = fallback
                        continue

                return ToolResult(
                    output=self._build_result_xml(
                        agent=subagent_type,
                        status="failed",
                        result=f"Error after {attempt} attempt(s): {error_msg}",
                        elapsed_ms=elapsed,
                    )
                )

    def _resolve_model(self, params, subagent_type, parent_pid, parent_mid):
        user_model = params.get("model")
        if user_model:
            return self._parse_model_ref(user_model)

        # 使用带兜底的路由选择
        default_model = self._registry.default_model
        return self._router.select_with_fallback(
            subagent_type,
            self._registry,
            parent_pid,
            parent_mid,
            default_model,
        )

    def _parse_model_ref(self, ref: str) -> tuple[str, str]:
        if "/" in ref:
            parts = ref.split("/", 1)
            return (parts[0], parts[1])
        return (ref, ref)

    def _build_no_model_error(self, subagent_type, parent_pid, parent_mid):
        available = self._registry.detected_providers
        parent_info = f"{parent_pid}/{parent_mid}" if parent_pid else "none"
        return (
            f"No suitable model available for subagent type '{subagent_type}'. "
            f"Parent model: {parent_info}. "
            f"Available providers with API keys: {available if available else 'none'}. "
            f"Unavailable models: {self._router.unavailable_models}. "
            f"Configure an API key via environment variable "
            f"(e.g. DEEPSEEK_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY) "
            f"or retry with a specific model parameter."
        )

    def _build_worker_context(self, params: dict, agent) -> str:
        """构建子 agent 上下文 — 精简，不传父消息历史。

        对应 Claude Code: Worker 看不到用户对话，
        只看到任务 spec + system prompt。

        使用 WorkerContextBuilder 实现三层隔离：
        - 只传递任务描述 + 相关文件 + agent prompt
        - 不传递父 session 的完整历史
        """
        # 构建任务规格
        task_spec = TaskSpec(
            description=params["description"],
            prompt=params["prompt"],
            relevant_files=params.get("relevant_files", []),
        )

        # 使用 WorkerContextBuilder 构建精简上下文
        context = self._context_builder.build(
            task_spec=task_spec,
            agent_type=params["subagent_type"],
        )

        # 追加 agent 的 system prompt
        if agent.system_prompt:
            context += f"\n\n{agent.system_prompt}"

        return context

    def _extract_text(self, messages: list) -> str:
        """从子 agent 最后一条 assistant 消息提取文本。"""
        for msg in reversed(messages):
            if msg.role != "assistant":
                continue
            parts = json.loads(msg.parts or "[]")
            texts = [p["text"] for p in parts if p.get("type") == "text"]
            if texts:
                return "\n".join(texts)
        return ""

    def _compute_usage(self, messages: list) -> dict | None:
        """从子 agent 消息中提取累计 token 和工具调用次数的使用统计。"""
        total_tokens = 0
        tool_calls = 0
        for msg in messages:
            if msg.usage:
                try:
                    u = json.loads(msg.usage)
                    total_tokens += u.get("total_tokens", 0) or (
                        u.get("input_tokens", 0) + u.get("output_tokens", 0)
                    )
                except Exception:
                    pass
            parts = json.loads(msg.parts or "[]")
            tool_calls += sum(1 for p in parts if p.get("type") == "tool_call")
        if total_tokens or tool_calls:
            return {"total_tokens": total_tokens, "tool_calls": tool_calls}
        return None

    def _build_result_xml(
        self,
        *,
        agent: str,
        status: str,
        result: str,
        usage: dict | None = None,
        elapsed_ms: int = 0,
    ) -> str:
        """构建 <task-result> XML — 子→父通信的唯一通道。

        对应 Claude Code 的 task-notification XML 注入。
        """
        lines = [
            "<task-result>",
            f"  <agent>{agent}</agent>",
            f"  <status>{status}</status>",
            f"  <result>{result}</result>",
        ]
        if usage:
            lines.append(
                f'  <usage total_tokens="{usage["total_tokens"]}"'
                f' tool_calls="{usage["tool_calls"]}" />'
            )
        if elapsed_ms:
            lines.append(f"  <duration_ms>{elapsed_ms}</duration_ms>")
        lines.append("</task-result>")
        return "\n".join(lines)
