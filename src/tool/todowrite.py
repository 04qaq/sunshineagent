"""TodoWrite tool: manage structured task lists."""


from src.tool.base import Tool, ToolContext, ToolResult


class TodoWriteTool(Tool):
    name = "todowrite"
    description = "Create and maintain a structured task list for the current session."
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"],
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": ["content", "status", "priority"],
                },
            },
        },
        "required": ["todos"],
    }

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        todos = params["todos"]
        lines = ["Task list updated:"]
        for i, t in enumerate(todos, 1):
            status_emoji = {
                "pending": "[ ]",
                "in_progress": "[~]",
                "completed": "[x]",
                "cancelled": "[-]",
            }.get(t["status"], "[ ]")
            lines.append(f"  {i}. {status_emoji} [{t['priority']}] {t['content']}")
        return ToolResult(
            output="\n".join(lines),
            metadata={"todos": todos},
        )
