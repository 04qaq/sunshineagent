"""Task tool: create subagent tasks."""

import json

from src.tool.base import Tool, ToolContext, ToolResult


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
                "enum": ["general", "explore"],
            },
            "model": {
                "type": "string",
                "description": "Optional model override",
            },
            "run_in_background": {"type": "boolean", "default": False},
        },
        "required": ["description", "prompt", "subagent_type"],
    }

    def __init__(self, sessions, agents, loop_factory, background_jobs):
        self._sessions = sessions
        self._agents = agents
        self._loop_factory = loop_factory
        self._jobs = background_jobs

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        subagent_type = params["subagent_type"]
        agent = self._agents.get(subagent_type)
        if not agent:
            return ToolResult(output=f"Unknown subagent type: {subagent_type}")

        child = await self._sessions.create(
            parent_id=ctx.session_id,
            agent=subagent_type,
            title=params["description"],
            provider_id=params.get("model"),
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
            provider_id=params.get("model", "anthropic"),
            model_id=params.get("model", "claude-sonnet-4-6"),
            max_steps=agent.max_steps,
            permission=agent.permission or PermissionRuleset.default(),
        )

        if params.get("run_in_background"):

            async def _run_worker():
                loop = self._loop_factory()
                return await loop.run(run_ctx)

            await self._jobs.start(child.id, _run_worker())
            return ToolResult(
                task_id=child.id,
                output=f"Task started in background. session_id={child.id}",
            )

        loop = self._loop_factory()
        await loop.run(run_ctx)

        messages = await self._sessions.get_messages(child.id)
        last = messages[-1] if messages else None
        if last:
            parts = json.loads(last.parts)
            text_parts = [p["text"] for p in parts if p["type"] == "text"]
            return ToolResult(output="\n".join(text_parts))

        return ToolResult(output="Task completed with no output.")

    def _build_worker_context(self, params: dict, agent) -> str:
        lines = [
            f"Task: {params['description']}",
            f"Goal: {params['prompt']}",
            "",
            agent.system_prompt or "",
        ]
        return "\n".join(filter(None, lines))
