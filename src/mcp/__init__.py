"""MCP integration client."""

from dataclasses import dataclass, field


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)


class MCPClient:
    def __init__(self):
        self._sessions: dict[str, object] = {}
        self._tools: dict[str, dict] = {}

    async def connect(self, config: MCPServerConfig):
        raise NotImplementedError("MCP client not yet fully implemented")

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        raise NotImplementedError("MCP client not yet fully implemented")

    def list_tools(self) -> list[dict]:
        return list(self._tools.values())

    async def disconnect_all(self):
        self._sessions.clear()
        self._tools.clear()
