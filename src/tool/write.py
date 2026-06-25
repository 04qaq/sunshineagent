"""Write tool.

Ownership: Human module. This is a stub until the human implements it.
"""

from src.tool.base import Tool, ToolContext, ToolResult


class WriteTool(Tool):
    name = "write"
    description = "Writes a file to the local filesystem."
    parameters = {
        "type": "object",
        "properties": {
            "filePath": {
                "type": "string",
                "description": "The absolute path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["filePath", "content"],
    }

    def __init__(self, workspace_root: str):
        raise NotImplementedError("Human module stub")

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError("Human module stub")
