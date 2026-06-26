"""Read 工具 —— 读取文件内容，返回带行号的结果。

OWNER: Human
SKILL: async file I/O, pathlib, 路径安全校验

功能：
  1. 支持绝对路径和相对路径（相对路径以 workspace_root 为基准）
  2. 安全检查：拒绝访问 workspace 之外的路径
  3. 支持 offset（起始行号）和 limit（最大行数）分页
  4. 输出格式：每行以 "行号\t内容" 开头
  5. 文件不存在时返回错误信息，不抛异常
"""

from pathlib import Path

from src.tool.base import Tool, ToolContext, ToolResult


class ReadTool(Tool):
    """文件读取工具。

    对应 OpenCode 的 read tool：
    https://github.com/anomalyco/opencode/blob/main/packages/core/src/tool/read.ts
    """

    name = "read"
    description = "Reads a file from the local filesystem."
    parameters = {
        "type": "object",
        "properties": {
            "filePath": {
                "type": "string",
                "description": "要读取的文件绝对路径",
            },
            "offset": {
                "type": "integer",
                "description": "起始行号（1-indexed，即第一行是 1）",
            },
            "limit": {
                "type": "integer",
                "description": "最多读取的行数",
            },
        },
        "required": ["filePath"],
    }

    def __init__(self, workspace_root: str):
        self._workspace = Path(workspace_root)

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        file_path = self._resolve_path(params["filePath"])
        offset = params.get("offset", 1)
        limit = params.get("limit")

        if not file_path.is_relative_to(self._workspace):
            return ToolResult(output="Access denied: path outside workspace")

        try:
            content = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ToolResult(output=f"File not found: {file_path}")
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}")

        lines = content.splitlines()
        start = max(0, offset - 1)
        lines = lines[start : start + limit] if limit is not None else lines[start:]

        numbered = "\n".join(
            f"{i + offset}\t{line}" for i, line in enumerate(lines)
        )
        return ToolResult(output=numbered)

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute():
            p = self._workspace / p
        return p.resolve()
