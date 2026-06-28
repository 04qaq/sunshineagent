"""权限确认 UI 模块。

借鉴 opencode 的权限确认设计：
- 显示工具信息
- 显示操作描述
- 支持 allow/deny/always 选项
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text


@dataclass
class PermissionRequest:
    """权限请求。"""
    tool: str
    description: str
    patterns: list[str] = None
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.patterns is None:
            self.patterns = []
        if self.metadata is None:
            self.metadata = {}


class PermissionUI:
    """权限确认 UI。

    借鉴 opencode 的权限确认设计：
    - 显示工具名称和描述
    - 显示操作模式
    - 支持 y/n/a 选择
    """

    def __init__(self, console: Console | None = None):
        self._console = console or Console()
        self._always_allow: set[str] = set()

    def ask(self, request: PermissionRequest) -> bool:
        """询问用户权限。

        Args:
            request: 权限请求

        Returns:
            是否允许
        """
        # 检查是否总是允许
        if request.tool in self._always_allow:
            return True

        # 构建显示内容
        content = Text()
        content.append("工具: ", style="dim")
        content.append(request.tool, style="bold cyan")
        content.append("\n")

        if request.description:
            content.append("操作: ", style="dim")
            content.append(request.description, style="white")
            content.append("\n")

        if request.patterns:
            content.append("目标: ", style="dim")
            content.append(", ".join(request.patterns), style="yellow")
            content.append("\n")

        # 显示面板
        self._console.print(Panel(
            content,
            title="权限确认",
            border_style="yellow",
        ))

        # 获取用户选择
        while True:
            choice = Prompt.ask(
                "选择",
                choices=["y", "n", "a"],
                default="n",
                console=self._console,
            )

            if choice == "y":
                return True
            elif choice == "n":
                return False
            elif choice == "a":
                # 总是允许此工具
                self._always_allow.add(request.tool)
                self._console.print(
                    f"[dim]已设置总是允许 {request.tool}[/dim]"
                )
                return True

    def clear_always_allow(self, tool: str | None = None):
        """清除总是允许设置。

        Args:
            tool: 工具名称，None 表示清除所有
        """
        if tool:
            self._always_allow.discard(tool)
        else:
            self._always_allow.clear()

    def is_always_allowed(self, tool: str) -> bool:
        """检查工具是否总是允许。"""
        return tool in self._always_allow


class QuestionUI:
    """问题询问 UI。

    用于 Agent 向用户提问。
    """

    def __init__(self, console: Console | None = None):
        self._console = console or Console()

    def ask(
        self,
        question: str,
        options: list[str] | None = None,
        default: str = "",
    ) -> str:
        """询问用户问题。

        Args:
            question: 问题文本
            options: 选项列表（可选）
            default: 默认值

        Returns:
            用户回答
        """
        # 显示问题
        self._console.print(Panel(
            question,
            title="问题",
            border_style="blue",
        ))

        # 获取回答
        if options:
            return Prompt.ask(
                "选择",
                choices=options,
                default=default or options[0],
                console=self._console,
            )
        else:
            return Prompt.ask(
                "回答",
                default=default,
                console=self._console,
            )
