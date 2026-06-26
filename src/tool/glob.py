"""Glob 工具 —— 基于通配符的文件模式匹配。

OWNER: Human
SKILL: pathlib, glob pattern matching

功能：
  1. 支持 glob 通配符（**/*.py 等）
  2. 支持指定搜索根目录（默认 workspace）
  3. 结果按文件修改时间降序排列
  4. 自动排除 __pycache__、.git、.venv 等目录
  5. 结果数量有上限保护，防止输出爆炸
"""

from pathlib import Path

from src.tool.base import Tool, ToolContext, ToolResult

# 搜索时自动跳过的目录名
_SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    ".eggs",
    "*.egg-info",
}


class GlobTool(Tool):
    """文件模式匹配工具。

    对应 OpenCode 的 glob tool：
    https://github.com/anomalyco/opencode/blob/main/packages/core/src/tool/glob.ts
    """

    name = "glob"
    description = "Fast file pattern matching tool."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "glob 模式，如 **/*.py 表示递归搜索所有 Python 文件",
            },
            "path": {
                "type": "string",
                "description": "搜索的根目录。不传则使用工作区根目录",
            },
        },
        "required": ["pattern"],
    }

    # 最大返回结果数，防止 LLM 上下文爆炸
    _MAX_RESULTS = 500

    def __init__(self, workspace_root: str):
        self._workspace = Path(workspace_root)

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        pattern = params["pattern"]
        search_path = Path(params.get("path", self._workspace))

        if not search_path.is_absolute():
            search_path = self._workspace / search_path
        search_path = search_path.resolve()

        if not search_path.is_relative_to(self._workspace):
            return ToolResult(output="Access denied: path outside workspace")

        if not search_path.exists():
            return ToolResult(output=f"Path not found: {search_path}")

        found = []
        for f in search_path.rglob(pattern):
            if not f.is_file():
                continue
            if any(part in _SKIP_DIRS for part in f.parts):
                continue
            found.append(f)

        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        truncated = False
        if len(found) > self._MAX_RESULTS:
            found = found[: self._MAX_RESULTS]
            truncated = True

        if not found:
            return ToolResult(output="No files matched")

        lines = [str(f) for f in found]
        if truncated:
            lines.append(f"... ({self._MAX_RESULTS} results shown, more not displayed)")

        return ToolResult(output="\n".join(lines))
