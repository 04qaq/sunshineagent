"""Grep tool: content search using regex."""

import re
from pathlib import Path

from src.tool.base import Tool, ToolContext, ToolResult


class GrepTool(Tool):
    name = "grep"
    description = "Fast content search tool that works with any codebase size."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in",
            },
            "include": {
                "type": "string",
                "description": "File pattern to filter (e.g. *.py)",
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace_root: str):
        self._workspace = Path(workspace_root)

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        pattern = params["pattern"]
        search_path = Path(params.get("path", self._workspace))
        include_pattern = params.get("include")

        if not search_path.is_absolute():
            search_path = self._workspace / search_path

        if not search_path.is_relative_to(self._workspace):
            return ToolResult(output="Access denied: path outside workspace")

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(output=f"Invalid regex pattern: {e}")

        results: list[str] = []
        files = list(search_path.rglob("*"))
        if include_pattern:
            import fnmatch

            files = [f for f in files if fnmatch.fnmatch(f.name, include_pattern)]

        files = [f for f in files if f.is_file()]
        files.sort()

        for f in files[:500]:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(content.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{f}:{i}: {line[:200]}")
                        if len(results) >= 200:
                            break
            except Exception:
                continue

            if len(results) >= 200:
                results.append("... [results truncated]")
                break

        return ToolResult(output="\n".join(results) or "No matches found")
