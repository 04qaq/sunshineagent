"""LSP tool: Language Server Protocol integration (stub)."""

from src.tool.base import Tool, ToolContext, ToolResult


class LSPTool(Tool):
    name = "lsp"
    description = "Interact with Language Server Protocol for code intelligence."
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["goToDefinition", "findReferences", "hover", "completion"],
                "description": "The LSP action to perform",
            },
            "filePath": {
                "type": "string",
                "description": "The file to operate on",
            },
            "line": {
                "type": "integer",
                "description": "The line number (1-indexed)",
            },
            "character": {
                "type": "integer",
                "description": "The character offset (0-indexed)",
            },
        },
        "required": ["action", "filePath", "line", "character"],
    }

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(output="LSP integration not yet implemented")
