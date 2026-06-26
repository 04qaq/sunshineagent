"""Write 工具 —— 将内容写入文件，文件不存在则创建。

OWNER: Human
SKILL: async file I/O, pathlib, 路径安全校验

功能：
  1. 支持绝对路径和相对路径
  2. 安全检查：拒绝访问 workspace 之外的路径
  3. 文件不存在时自动创建（含父目录）
  4. 文件已存在时直接覆盖
  5. 返回成功/失败信息
"""

from pathlib import Path

from src.tool.base import Tool, ToolContext, ToolResult


class WriteTool(Tool):
    """文件写入工具。

    对应 OpenCode 的 write tool。
    """

    name = "write"
    description = "Writes a file to the local filesystem."
    parameters = {
        "type": "object",
        "properties": {
            "filePath": {
                "type": "string",
                "description": "要写入的文件绝对路径",
            },
            "content": {
                "type": "string",
                "description": "要写入的文件内容",
            },
        },
        "required": ["filePath", "content"],
    }

    def __init__(self, workspace_root: str):
        self._workspace = Path(workspace_root)

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        file_path = self._resolve_path(params["filePath"])
        content = params["content"]

        if not file_path.is_relative_to(self._workspace):
            return ToolResult(output="Access denied: path outside workspace")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return ToolResult(output=f"Wrote {len(content)} bytes to {file_path}")

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute():
            p = self._workspace / p
        return p.resolve()
