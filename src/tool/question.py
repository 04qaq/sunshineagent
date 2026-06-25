"""Question tool: ask the user a question."""

from src.tool.base import Tool, ToolContext, ToolResult


class QuestionTool(Tool):
    name = "question"
    description = "Ask the user a question during execution."
    parameters = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "header": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label", "description"],
                            },
                        },
                        "multiple": {"type": "boolean"},
                    },
                    "required": ["question", "header", "options"],
                },
            },
        },
        "required": ["questions"],
    }

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        if ctx.ask_callback:
            result = await ctx.ask_callback(params["questions"])
            return ToolResult(output=str(result))
        return ToolResult(
            output="Ask callback not available. Questions: "
            + str(params["questions"])
        )
