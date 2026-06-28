"""ApplyPatch tool: apply a unified diff patch to files."""

from pathlib import Path

from src.tool.base import Tool, ToolContext, ToolResult


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = "Apply a unified diff patch to files in the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": "The unified diff patch to apply",
            },
        },
        "required": ["patch"],
    }

    def __init__(self, workspace_root: str):
        self._workspace = Path(workspace_root)

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        patch_content = params["patch"]
        if not patch_content.strip():
            return ToolResult(output="Empty patch provided")

        lines = patch_content.splitlines()
        current_file: str | None = None
        hunks: list[list[str]] = []
        current_hunk: list[str] = []

        for line in lines:
            if line.startswith("--- "):
                continue
            if line.startswith("+++ "):
                if current_hunk:
                    hunks.append(current_hunk)
                    current_hunk = []
                path = line[4:].strip()
                if path.startswith("a/") or path.startswith("b/"):
                    path = path[2:]
                current_file = path
                continue
            if current_file:
                current_hunk.append(line)

        if current_hunk:
            hunks.append(current_hunk)

        if not current_file:
            return ToolResult(output="Could not parse patch file path")

        file_path = self._resolve_path(current_file)
        if not file_path.is_relative_to(self._workspace):
            return ToolResult(output="Access denied: path outside workspace")

        if not file_path.exists():
            return ToolResult(output=f"File not found: {file_path}")

        try:
            content = file_path.read_text(encoding="utf-8").splitlines()
            new_content = self._apply_hunks(content, hunks)
            file_path.write_text("\n".join(new_content) + "\n", encoding="utf-8")
            return ToolResult(output=f"Patch applied to {file_path}")
        except Exception as e:
            return ToolResult(output=f"Failed to apply patch: {e}")

    def _apply_hunks(self, lines: list[str], hunks: list[list[str]]) -> list[str]:
        result = list(lines)
        for hunk in hunks:
            line_idx = 0
            for hunk_line in hunk:
                if hunk_line.startswith("@@"):
                    parts = hunk_line.split("@@")
                    if len(parts) >= 2:
                        range_part = parts[1].strip()
                        if range_part.startswith("-"):
                            range_part = range_part[1:]
                        comma_idx = (
                            range_part.find(",")
                            if "," in range_part
                            else len(range_part)
                        )
                        line_idx = max(
                            0,
                            int(range_part[:comma_idx].split("+")[0].split("-")[0]) - 1,
                        )
                elif hunk_line.startswith(" "):
                    line_idx += 1
                elif hunk_line.startswith("-"):
                    result[line_idx] = hunk_line[1:]
                elif hunk_line.startswith("+"):
                    result.insert(line_idx, hunk_line[1:])
                    line_idx += 1
        return result

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute():
            p = self._workspace / p
        return p.resolve()
