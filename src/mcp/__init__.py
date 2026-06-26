"""MCP (Model Context Protocol) 集成。

通过 stdio 连接外部 MCP server，发现工具并注册到 ToolRegistry。
"""

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters
from src.tool.base import Tool, ToolContext, ToolResult


@dataclass
class MCPServerConfig:
    """单个 MCP server 的启动配置。

    对应 OpenCode 的 MCP server 定义：
    - name: 唯一标识符，用于拼接工具名 mcp__<name>__<tool>
    - command: 可执行命令 (如 "node", "python")
    - args: 命令参数 (如 ["server.js"])
    """

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)


class MCPTool(Tool):
    """MCP 工具的 Tool 子类包装器。

    将 MCP server 发现的工具包装为 SunshineAgent Tool 接口，
    使权限系统和 ToolRegistry 可以统一管理。
    """

    name = ""
    description = ""
    parameters = {}

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        server_name: str,
        original_name: str,
        mcp_client,
    ):
        self.name = name
        self.description = description or name
        self.parameters = input_schema or {"type": "object", "properties": {}}
        self._server = server_name
        self._original = original_name
        self._client = mcp_client

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        try:
            result = await self._client.call_tool(
                self._server, self._original, params
            )
            return ToolResult(output=result)
        except Exception as e:
            return ToolResult(output=f"MCP tool error: {e}")


class MCPClient:
    """MCP 客户端 —— 管理多个 MCP server 的连接、工具发现和调用。

    对应 OpenCode mcp/index.ts。

    使用方式：
        client = MCPClient()
        await client.connect(MCPServerConfig(
            name="filesystem", command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "."]
        ))
        tools = client.list_tools()  # → 可注册到 ToolRegistry
    """

    def __init__(self):
        self._sessions: dict[str, ClientSession] = {}
        self._transports: dict[str, tuple] = {}
        self._tools: dict[str, dict] = {}

    async def connect(self, config: MCPServerConfig) -> list[MCPTool]:
        """连接 MCP server，发现工具并返回 MCPTool 列表。"""
        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env={**config.env} if config.env else None,
        )

        transport = await stdio_client(server_params)
        read, write = transport
        session = ClientSession(read, write)
        await session.initialize()

        self._sessions[config.name] = session
        self._transports[config.name] = transport

        result = await session.list_tools()
        tools = []
        for t in result.tools:
            full_name = f"mcp__{config.name}__{t.name}"
            tool_def = {
                "name": full_name,
                "description": t.description or full_name,
                "input_schema": t.inputSchema or {},
                "server": config.name,
                "original_name": t.name,
            }
            self._tools[full_name] = tool_def
            tools.append(
                MCPTool(
                    name=full_name,
                    description=tool_def["description"],
                    input_schema=tool_def["input_schema"],
                    server_name=config.name,
                    original_name=t.name,
                    mcp_client=self,
                )
            )
        return tools

    async def call_tool(self, server: str, tool_name: str, arguments: dict) -> str:
        """调用 MCP server 上的指定工具。"""
        session = self._sessions.get(server)
        if not session:
            raise ValueError(f"MCP server not connected: {server}")
        result = await session.call_tool(tool_name, arguments)
        return json.dumps(result.content, ensure_ascii=False)

    def list_tools(self) -> list[dict]:
        """列出所有已发现的 MCP 工具定义。"""
        return list(self._tools.values())

    async def disconnect(self, name: str):
        """断开指定 MCP server。"""
        with contextlib.suppress(Exception):
            if name in self._sessions:
                await self._sessions[name].close()
                del self._sessions[name]
        if name in self._transports:
            del self._transports[name]
        self._tools = {
            k: v for k, v in self._tools.items() if v["server"] != name
        }

    async def disconnect_all(self):
        """断开所有 MCP server。"""
        for name in list(self._sessions.keys()):
            await self.disconnect(name)


def load_mcp_config(path: str | None = None) -> list[MCPServerConfig]:
    """从 JSON 配置文件加载 MCP server 配置。

    默认路径：~/.sunshine/mcp.json

    格式：
    {
      "servers": [
        {
          "name": "filesystem",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
          "env": {}
        }
      ]
    }
    """
    filepath = Path(path) if path else Path.home() / ".sunshine" / "mcp.json"
    if not filepath.exists():
        return []

    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    servers = data.get("servers", data.get("mcpServers", []))
    configs = []
    for s in servers:
        configs.append(
            MCPServerConfig(
                name=s["name"],
                command=s["command"],
                args=s.get("args", []),
                env=s.get("env", {}),
            )
        )
    return configs
