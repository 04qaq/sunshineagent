"""WebSearch tool: web search (stub)."""

from src.tool.base import Tool, ToolContext, ToolResult


class WebSearchTool(Tool):
    name = "websearch"
    description = "Search the web for information."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query",
            },
        },
        "required": ["query"],
    }

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(output="Web search not yet implemented. Configure a search API.")
