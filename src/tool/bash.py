"""Bash tool: execute shell commands."""

import asyncio
from pathlib import Path

from src.tool.base import Tool, ToolContext, ToolResult


class BashTool(Tool):
    name = "bash"
    description = "Executes a shell command with optional timeout."
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in milliseconds",
            },
            "workdir": {
                "type": "string",
                "description": "The working directory for the command",
            },
            "description": {
                "type": "string",
                "description": "Short description of what this command does",
            },
        },
        "required": ["command", "description"],
    }

    def __init__(self, workspace_root: str):
        self._workspace = Path(workspace_root)

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        command = params["command"]
        timeout = params.get("timeout", 120000) / 1000.0
        workdir = params.get("workdir", str(self._workspace))

        if not Path(workdir).is_relative_to(self._workspace):
            return ToolResult(output="Access denied: workdir outside workspace")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\n[stderr]\n" + stderr.decode("utf-8", errors="replace")

            if len(output) > 50000:
                output = output[:50000] + "\n... [output truncated]"

            return ToolResult(output=output or "(no output)")
        except TimeoutError:
            return ToolResult(output=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(output=f"Command failed: {e}")
