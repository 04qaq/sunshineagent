"""Glob tool.

Ownership: Human module. This is a stub until the human implements it.
"""

from src.tool.base import Tool, ToolContext, ToolResult


class GlobTool(Tool):
    name = "glob"
    description = "Fast file pattern matching tool."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in",
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace_root: str):
        raise NotImplementedError("Human module stub")

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError("Human module stub")
