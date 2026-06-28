"""Tool Filter - 三层工具过滤模型。

借鉴 Claude Code 的设计：
- 第一层：全局禁止列表（防递归、安全）
- 第二层：Agent 类型约束（白名单/黑名单）
- 第三层：Agent 自身声明
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 第一层：全局禁止列表
# 所有子 Agent 都不能使用的工具
GLOBAL_DISALLOWED_TOOLS: set[str] = {
    "task",           # 防止无限递归派发
    "question",       # 后台 Agent 不能弹窗
    "plan_exit",      # 不能替父退出计划模式
    "skill",          # 技能工具保留给主 Agent
}

# 第二层：Agent 类型约束
# 白名单模式：只允许指定工具
# 黑名单模式：禁止指定工具，其他允许

@dataclass
class AgentToolConstraints:
    """Agent 工具约束。"""
    # 白名单模式（如果设置，只允许这些工具）
    allowed_tools: set[str] | None = None

    # 黑名单模式（禁止这些工具）
    disallowed_tools: set[str] = field(default_factory=set)

    # 是否使用白名单模式
    use_allowlist: bool = False


# 不同 Agent 类型的默认约束
AGENT_TOOL_CONSTRAINTS: dict[str, AgentToolConstraints] = {
    "general": AgentToolConstraints(
        # 黑名单模式：继承全局禁止
        disallowed_tools=GLOBAL_DISALLOWED_TOOLS.copy(),
        use_allowlist=False,
    ),
    "explore": AgentToolConstraints(
        # 白名单模式：只读工具
        allowed_tools={"read", "glob", "grep", "lsp"},
        use_allowlist=True,
    ),
    "code": AgentToolConstraints(
        # 黑名单模式：大部分工具可用
        disallowed_tools=GLOBAL_DISALLOWED_TOOLS.copy(),
        use_allowlist=False,
    ),
    "test": AgentToolConstraints(
        # 黑名单模式：大部分工具可用
        disallowed_tools=GLOBAL_DISALLOWED_TOOLS.copy(),
        use_allowlist=False,
    ),
    "document": AgentToolConstraints(
        # 白名单模式：只读 + 写文件
        allowed_tools={"read", "glob", "grep", "write"},
        use_allowlist=True,
    ),
    "search": AgentToolConstraints(
        # 白名单模式：只读工具
        allowed_tools={"read", "glob", "grep", "webfetch", "websearch"},
        use_allowlist=True,
    ),
}


class ToolFilter:
    """工具过滤器 - 三层过滤模型。

    对应 Claude Code 的 filterToolsForAgent() 和 resolveAgentTools()。
    """

    def __init__(
        self,
        global_disallowed: set[str] | None = None,
        agent_constraints: dict[str, AgentToolConstraints] | None = None,
    ):
        self._global_disallowed = global_disallowed or GLOBAL_DISALLOWED_TOOLS
        self._agent_constraints = agent_constraints or AGENT_TOOL_CONSTRAINTS

    def filter_tools(
        self,
        available_tools: list[str],
        agent_type: str,
        agent_allowed: list[str] | None = None,
        agent_disallowed: list[str] | None = None,
    ) -> list[str]:
        """过滤工具列表。

        三层过滤：
        1. 全局禁止列表
        2. Agent 类型约束
        3. Agent 自身声明

        Args:
            available_tools: 可用工具列表
            agent_type: Agent 类型
            agent_allowed: Agent 自身声明的允许工具（第三层）
            agent_disallowed: Agent 自身声明的禁止工具（第三层）

        Returns:
            过滤后的工具列表
        """
        tools = available_tools.copy()

        # 第一层：全局禁止
        tools = [t for t in tools if t not in self._global_disallowed]

        # 第二层：Agent 类型约束
        constraints = self._agent_constraints.get(agent_type)
        if constraints:
            if constraints.use_allowlist and constraints.allowed_tools is not None:
                # 白名单模式：只允许指定工具
                tools = [t for t in tools if t in constraints.allowed_tools]
            else:
                # 黑名单模式：禁止指定工具
                tools = [t for t in tools if t not in constraints.disallowed_tools]

        # 第三层：Agent 自身声明
        if agent_allowed is not None:
            # 白名单模式
            allowed_set = set(agent_allowed)
            tools = [t for t in tools if t in allowed_set]
        elif agent_disallowed is not None:
            # 黑名单模式
            disallowed_set = set(agent_disallowed)
            tools = [t for t in tools if t not in disallowed_set]

        return tools

    def get_constraints(self, agent_type: str) -> AgentToolConstraints | None:
        """获取 Agent 类型的约束。"""
        return self._agent_constraints.get(agent_type)

    def set_constraints(self, agent_type: str, constraints: AgentToolConstraints):
        """设置 Agent 类型的约束。"""
        self._agent_constraints[agent_type] = constraints

    def add_global_disallowed(self, tool_name: str):
        """添加全局禁止工具。"""
        self._global_disallowed.add(tool_name)

    def remove_global_disallowed(self, tool_name: str):
        """移除全局禁止工具。"""
        self._global_disallowed.discard(tool_name)
