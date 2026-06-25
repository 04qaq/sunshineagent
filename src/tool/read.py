"""Read tool.

Ownership: Human module. This is a stub until the human implements it.
"""


from src.tool.base import Tool, ToolContext, ToolResult


class ReadTool(Tool):
    name = "read"
    description = "Reads a file from the local filesystem."
    parameters = {
        "type": "object",
        "properties": {
            "filePath": {
                "type": "string",
                "description": "The absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from (1-indexed)",
            },
            "limit": {
                "type": "integer",
                "description": "The maximum number of lines to read",
            },
        },
        "required": ["filePath"],
    }

    def __init__(self, workspace_root: str):
        raise NotImplementedError("Human module stub")

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError("Human module stub")
