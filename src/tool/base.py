"""Tool 基类 —— 定义所有工具的抽象接口。

OWNER: Human
SKILL: abstract class, async file I/O, pathlib

核心数据结构：
  ToolContext  — 工具执行上下文（会话ID、Agent名称、中断信号等）
  ToolResult   — 工具执行结果（输出文本、元数据、子任务ID等）
  Tool (ABC)   — 抽象基类，所有工具必须实现 execute() 方法
  ToolRegistry — 工具注册表，按名称管理工具，支持按 Agent 权限过滤
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class ToolContext:
    """工具执行上下文 —— 对应 OpenCode 的富 context。

    每次工具调用时由 AgentLoop._settle_tools() 创建并传入。
    """

    # 所属会话的 ULID
    session_id: str

    # 当前 Agent 名称（如 "build", "general"）
    agent: str

    # 触发此次工具调用的 assistant 消息 ID（可能尚未入库，可为 None）
    assistant_message_id: str | None

    # 本次工具调用的唯一 ID（LLM 生成的 tool_call_id）
    tool_call_id: str

    # 中断信号：外部可 set() 来请求工具提前终止
    abort_signal: asyncio.Event = field(default_factory=asyncio.Event)

    # 提问回调：question 工具通过此回调向用户发起交互式提问
    ask_callback: Callable[..., Awaitable[None]] | None = None


@dataclass
class ToolResult:
    """工具执行结果 —— 对应 OpenCode 的 ExecuteResult。

    所有工具的 execute() 方法必须返回此类型。
    """

    # 结果标题（可选，用于 Rich 渲染）
    title: str | None = None

    # 主要输出文本（会截断后追加到 LLM 上下文）
    output: str = ""

    # 附加元数据（自由格式，可用于前端渲染优化）
    metadata: dict = field(default_factory=dict)

    # 子任务 ID（仅 task tool 使用，指向子 session 的 ULID）
    task_id: str | None = None


class Tool(ABC):
    """工具抽象基类 —— 所有工具必须继承此类并实现 execute()。

    子类需要定义 3 个类属性和 1 个方法：
      name: str          — 工具名称（LLM 通过此名称调用）
      description: str   — 工具用途描述（写入 system prompt）
      parameters: dict   — JSON Schema 格式的参数定义
      execute() -> ToolResult  — 异步执行逻辑
    """

    name: str = ""
    description: str = ""
    parameters: dict = {}

    @abstractmethod
    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        """执行工具。子类必须实现。

        Args:
            params: LLM 传入的参数字典，结构与 self.parameters 对应
            ctx: 工具执行上下文（会话信息、中断信号等）

        Returns:
            ToolResult: 执行结果（output 文本会被追加到 LLM 对话）
        """
        raise NotImplementedError

    def to_openai_tool(self) -> dict:
        """转换为 OpenAI Chat Completions 的 tool definition 格式。

        供 OpenAI Client 使用：将 self.parameters 嵌套在 function 节点下。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_tool(self) -> dict:
        """转换为 Anthropic Messages API 的 tool 格式。

        供 Anthropic Client 使用：将 self.parameters 作为顶层 input_schema。
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


class ToolRegistry:
    """工具注册表 —— 按名称管理所有工具。

    工具来源（注册表不关心来源，只负责存储和查询）：
      1. 内置工具（Agent 模块在启动时注册）
      2. MCP 工具（MCP 客户端连接后动态注册）
      3. Skill 工具（加载 skill 后注册）
      ───────────────────────────────────────────────
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        """注册工具。同名工具会覆盖旧值。"""
        self._tools[tool.name] = tool

    def unregister(self, name: str):
        """注销工具。不存在时静默忽略。"""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """按名称获取工具。不存在返回 None。"""
        return self._tools.get(name)

    def list_all(self) -> list[Tool]:
        """列出所有已注册工具。"""
        return list(self._tools.values())

    async def resolve_for_agent(self, agent) -> list[dict]:
        """为指定 Agent 解析可用工具列表（过滤权限）。

        Args:
            agent: AgentInfo 实例，包含 permission 属性

        Returns:
            list[dict]: 已转换为 Anthropic tool 格式的可用工具列表
        """
        tools = []
        for name, tool in self._tools.items():
            if agent.permission and agent.permission.can_use(name):
                tools.append(tool.to_anthropic_tool())
        return tools
