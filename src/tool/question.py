"""Question tool: ask the user a question."""

from src.tool.base import Tool, ToolContext, ToolResult


class QuestionTool(Tool):
    name = "question"
    description = """Ask the user a question during execution. Use this tool when you need to gather information from the user before proceeding. This is the preferred way to ask questions - do NOT just print questions in your response.

When to use:
- When you need clarification on requirements
- When you need the user to make a choice between options
- When you need more details before starting work
- When the user asks you to ask them questions

Do NOT use for:
- Rhetorical questions
- Questions you can answer yourself
- Simple acknowledgments

The tool presents questions with optional multiple-choice options and collects user answers."""
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
