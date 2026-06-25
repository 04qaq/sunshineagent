"""Tool base classes.

Ownership: Human module. This is a stub until the human implements it.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class ToolContext:
    session_id: str
    agent: str
    assistant_message_id: str | None
    tool_call_id: str
    abort_signal: asyncio.Event = field(default_factory=asyncio.Event)
    ask_callback: Callable[..., Awaitable[None]] | None = None


@dataclass
class ToolResult:
    title: str | None = None
    output: str = ""
    metadata: dict = field(default_factory=dict)
    task_id: str | None = None


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict = {}

    @abstractmethod
    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_tool(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_all(self) -> list[Tool]:
        return list(self._tools.values())

    async def resolve_for_agent(self, agent) -> list[dict]:

        tools = []
        for name, tool in self._tools.items():
            if agent.permission and agent.permission.can_use(name):
                tools.append(tool.to_anthropic_tool())
        return tools
