"""Edit tool: exact string replacement in files."""

from pathlib import Path

from src.tool.base import Tool, ToolContext, ToolResult


class EditTool(Tool):
    name = "edit"
    description = "Performs exact string replacements in files."
    parameters = {
        "type": "object",
        "properties": {
            "filePath": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "oldString": {
                "type": "string",
                "description": "The text to replace",
            },
            "newString": {
                "type": "string",
                "description": "The text to replace it with",
            },
            "replaceAll": {
                "type": "boolean",
                "description": "Replace all occurrences (default false)",
            },
        },
        "required": ["filePath", "oldString", "newString"],
    }

    def __init__(self, workspace_root: str):
        self._workspace = Path(workspace_root)

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        file_path = self._resolve_path(params["filePath"])
        old_str = params["oldString"]
        new_str = params["newString"]
        replace_all = params.get("replaceAll", False)

        if not file_path.is_relative_to(self._workspace):
            return ToolResult(output="Access denied: path outside workspace")

        if new_str == old_str:
            return ToolResult(output="newString must be different from oldString")

        try:
            content = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ToolResult(output=f"File not found: {file_path}")
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}")

        count = content.count(old_str)
        if count == 0:
            return ToolResult(output="oldString not found in file")
        if not replace_all and count > 1:
            return ToolResult(
                output=(
                    f"Found {count} matches for oldString. "
                    "Use replaceAll=true or provide more context to narrow the match."
                )
            )

        if replace_all:
            new_content = content.replace(old_str, new_str)
        else:
            new_content = content.replace(old_str, new_str, 1)

        file_path.write_text(new_content, encoding="utf-8")
        return ToolResult(output=f"Successfully edited {file_path}")

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute():
            p = self._workspace / p
        return p.resolve()
