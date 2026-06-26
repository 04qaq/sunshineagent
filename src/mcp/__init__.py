"""MCP (Model Context Protocol) 集成。

支持全局 MCP 和项目 MCP 的分层管理：
  - ~/.sunshine/mcp/  → 全局 MCP（需手动 enable 到项目）
  - <workspace>/.sunshine/mcp/  → 项目 MCP（自动加载，Agent 可见）

每个 .json 文件定义一个 MCP server，拖入目录即生效。
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
    """单个 MCP server 配置。"""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    source: str = ""  # "global" | "project"


class MCPTool(Tool):
    """MCP 工具包装器。"""

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
            result = await self._client.call_tool(self._server, self._original, params)
            return ToolResult(output=result)
        except Exception as e:
            return ToolResult(output=f"MCP tool error: {e}")


class MCPClient:
    """MCP 客户端 —— 管理多个 MCP server 的连接。"""

    def __init__(self):
        self._sessions: dict[str, ClientSession] = {}
        self._transports: dict[str, tuple] = {}
        self._tools: dict[str, dict] = {}

    async def connect(self, config: MCPServerConfig) -> list[MCPTool]:
        """连接 MCP server，发现工具。"""
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
            self._tools[full_name] = {
                "name": full_name,
                "description": t.description or full_name,
                "input_schema": t.inputSchema or {},
                "server": config.name,
                "original_name": t.name,
            }
            tools.append(
                MCPTool(
                    name=full_name,
                    description=t.description or full_name,
                    input_schema=t.inputSchema or {},
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
        """列出所有已连接的 MCP 工具。"""
        return list(self._tools.values())

    async def disconnect(self, name: str):
        """断开指定 MCP server。"""
        with contextlib.suppress(Exception):
            if name in self._sessions:
                await self._sessions[name].close()
                del self._sessions[name]
        if name in self._transports:
            del self._transports[name]
        self._tools = {k: v for k, v in self._tools.items() if v["server"] != name}

    async def disconnect_all(self):
        """断开所有 MCP server。"""
        for name in list(self._sessions.keys()):
            await self.disconnect(name)


# ── 配置扫描 ───────────────────────────────────────────────────────────


def _scan_mcp_dir(directory: Path, source: str) -> list[MCPServerConfig]:
    """扫描目录中的 .json 文件，解析为 MCP server 配置列表。"""
    configs: list[MCPServerConfig] = []
    if not directory.exists():
        return configs

    for f in sorted(directory.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # 支持单文件单个 server 和单文件多个 servers 两种格式
            items = data.get("servers", [data] if "name" in data else [])
            for item in items:
                if "name" not in item or "command" not in item:
                    continue
                configs.append(
                    MCPServerConfig(
                        name=item["name"],
                        command=item["command"],
                        args=item.get("args", []),
                        env=item.get("env", {}),
                        source=source,
                    )
                )
        except Exception:
            pass
    return configs


def _mcp_dir_global() -> Path:
    return Path.home() / ".sunshine" / "mcp"


def _mcp_dir_project(workspace: str) -> Path:
    return Path(workspace) / ".sunshine" / "mcp"


def load_global_configs() -> list[MCPServerConfig]:
    """扫描全局 MCP 配置（~/.sunshine/mcp/*.json）。"""
    return _scan_mcp_dir(_mcp_dir_global(), "global")


def load_project_configs(workspace: str) -> list[MCPServerConfig]:
    """扫描项目 MCP 配置（<workspace>/.sunshine/mcp/*.json）。"""
    return _scan_mcp_dir(_mcp_dir_project(workspace), "project")


def load_all_configs(workspace: str) -> list[MCPServerConfig]:
    """加载所有 MCP 配置：全局 + 项目。"""
    return load_global_configs() + load_project_configs(workspace)


def save_mcp_config(config: MCPServerConfig) -> Path:
    """保存 MCP 配置到磁盘。"""
    directory = (
        _mcp_dir_global() if config.source == "global"
        else _mcp_dir_project(Path(config.env.get("_workspace", "")).as_posix())
        if config.env.get("_workspace")
        else _mcp_dir_global()
    )
    directory.mkdir(parents=True, exist_ok=True)
    filepath = directory / f"{config.name}.json"
    data = {
        "name": config.name,
        "command": config.command,
        "args": config.args,
    }
    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return filepath


def remove_mcp_config(name: str, workspace: str) -> bool:
    """删除 MCP 配置文件。先查项目，再查全局。"""
    for directory in [_mcp_dir_project(workspace), _mcp_dir_global()]:
        filepath = directory / f"{name}.json"
        if filepath.exists():
            filepath.unlink()
            return True
    return False
