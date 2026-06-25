"""PlanExit tool: exit plan mode."""

from src.tool.base import Tool, ToolContext, ToolResult


class PlanExitTool(Tool):
    name = "plan_exit"
    description = "Signal that the plan/explore phase is complete and the agent should exit."
    parameters = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Summary of what was accomplished",
            },
        },
        "required": [],
    }

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        summary = params.get("summary", "Plan phase complete")
        return ToolResult(output=summary)
