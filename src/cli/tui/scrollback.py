"""Scrollback 视图组件。

显示会话历史，支持滚动浏览。
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static


class ScrollbackView(VerticalScroll):
    """会话历史视图。

    借鉴 opencode 的 Scrollback 设计：
    - 显示用户消息
    - 显示助手消息
    - 显示系统消息
    - 支持自动滚动
    """

    AUTO_SCROLL = reactive(True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._auto_scroll = True

    def compose(self):
        """初始为空，动态添加内容。"""
        yield Static("", id="scrollback-content")

    def on_mount(self) -> None:
        """挂载后初始化。"""
        self._content = self.query_one("#scrollback-content", Static)

    def add_message(self, role: str, content: str) -> None:
        """添加消息。

        Args:
            role: 消息角色 (user/assistant/system/error)
            content: 消息内容
        """
        # 构建消息文本
        if role == "user":
            prefix = "[bold green]> [/bold green]"
            css_class = "user-message"
        elif role == "assistant":
            prefix = ""
            css_class = "assistant-message"
        elif role == "system":
            prefix = "[dim]# [/dim]"
            css_class = "system-message"
        elif role == "error":
            prefix = "[red]! [/red]"
            css_class = "error-message"
        else:
            prefix = ""
            css_class = ""

        # 添加到视图
        entry = Static(f"{prefix}{content}", classes=f"scrollback-entry {css_class}")
        self._content.mount(entry)

        # 自动滚动到底部
        if self._auto_scroll:
            self.scroll_end(animate=False)

    def clear(self) -> None:
        """清空历史。"""
        self._content.remove_children()

    def toggle_auto_scroll(self) -> None:
        """切换自动滚动。"""
        self._auto_scroll = not self._auto_scroll
        self.AUTO_SCROLL = self._auto_scroll
