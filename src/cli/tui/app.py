"""SunshineAgent TUI 应用。

借鉴 opencode 的分屏布局设计：
- Scrollback：上半部分，显示会话历史
- Footer：下半部分，显示输入框和状态栏
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Header

from src.cli.tui.input_box import InputBox
from src.cli.tui.scrollback import ScrollbackView
from src.cli.tui.status_bar import TuiStatusBar


class SunshineApp(App):
    """SunshineAgent TUI 主应用。

    分屏布局：
    ┌─────────────────────────────────────────┐
    │ Header                                  │
    ├─────────────────────────────────────────┤
    │                                         │
    │ Scrollback (会话历史)                    │
    │                                         │
    ├─────────────────────────────────────────┤
    │ Status Bar (Agent/模型/时长/Token)       │
    ├─────────────────────────────────────────┤
    │ Input Box (用户输入)                     │
    ├─────────────────────────────────────────┤
    │ Footer (快捷键提示)                      │
    └─────────────────────────────────────────┘
    """

    CSS = """
    Screen {
        background: $surface;
    }

    #header {
        dock: top;
        height: 1;
    }

    #scrollback {
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
    }

    #input-container {
        dock: bottom;
        height: auto;
        min-height: 3;
        max-height: 10;
        padding: 0 1;
    }

    #input-box {
        height: auto;
        min-height: 1;
        max-height: 8;
    }

    #footer {
        dock: bottom;
        height: 1;
    }

    .scrollback-entry {
        margin: 0 0 1 0;
    }

    .user-message {
        color: $success;
    }

    .assistant-message {
        color: $text;
    }

    .system-message {
        color: $warning;
    }

    .error-message {
        color: $error;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "中断", show=True),
        Binding("ctrl+n", "new_session", "新会话", show=True),
        Binding("ctrl+l", "clear", "清屏", show=True),
        Binding("up", "history_up", "上一条", show=False),
        Binding("down", "history_down", "下一条", show=False),
    ]

    TITLE = "SunshineAgent"

    # 响应式状态
    current_agent: reactive[str] = reactive("build")
    current_model: reactive[str] = reactive("")
    is_running: reactive[bool] = reactive(False)

    class PromptSubmitted(Message):
        """提示提交消息。"""
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class InterruptRequested(Message):
        """中断请求消息。"""
        pass

    def __init__(
        self,
        agent_name: str = "build",
        model_name: str = "",
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.current_agent = agent_name
        self.current_model = model_name
        self._history: list[str] = []
        self._history_index: int | None = None
        self._draft: str = ""

    def compose(self) -> ComposeResult:
        """构建 UI 布局。"""
        yield Header(id="header")
        yield ScrollbackView(id="scrollback")
        yield TuiStatusBar(id="status-bar")
        yield InputBox(id="input-box")
        yield Footer(id="footer")

    def on_mount(self) -> None:
        """应用挂载后的初始化。"""
        # 聚焦输入框
        self.query_one("#input-box", InputBox).focus()

    def on_input_box_submitted(self, event: InputBox.Submitted) -> None:
        """处理输入提交。"""
        text = event.text.strip()
        if not text:
            return

        # 添加到历史记录
        self._history.append(text)
        self._history_index = None
        self._draft = ""

        # 发送提示提交消息
        self.post_message(self.PromptSubmitted(text))

        # 清空输入框
        event.input.value = ""

    def on_input_box_changed(self, event: InputBox.Changed) -> None:
        """处理输入变化。"""
        # 保存草稿（用于历史导航）
        if self._history_index is not None:
            self._draft = event.value

    def action_interrupt(self) -> None:
        """中断当前执行。"""
        self.post_message(self.InterruptRequested())

    def action_new_session(self) -> None:
        """创建新会话。"""
        self.query_one("#scrollback", ScrollbackView).clear()
        self.query_one("#input-box", InputBox).focus()

    def action_clear(self) -> None:
        """清屏。"""
        self.query_one("#scrollback", ScrollbackView).clear()

    def action_history_up(self) -> None:
        """浏览历史上一条。"""
        if not self._history:
            return

        input_box = self.query_one("#input-box", InputBox)

        if self._history_index is None:
            self._draft = input_box.value
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return

        input_box.value = self._history[self._history_index]

    def action_history_down(self) -> None:
        """浏览历史下一条。"""
        if self._history_index is None:
            return

        input_box = self.query_one("#input-box", InputBox)

        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            input_box.value = self._history[self._history_index]
        else:
            self._history_index = None
            input_box.value = self._draft

    def add_message(self, role: str, content: str) -> None:
        """添加消息到 scrollback。

        Args:
            role: 消息角色 (user/assistant/system/error)
            content: 消息内容
        """
        scrollback = self.query_one("#scrollback", ScrollbackView)
        scrollback.add_message(role, content)

    def update_status(self, **kwargs: Any) -> None:
        """更新状态栏。

        Args:
            agent: Agent 名称
            model: 模型名称
            duration: 执行时长
            tokens: Token 使用量
            running: 是否运行中
        """
        status_bar = self.query_one("#status-bar", TuiStatusBar)
        status_bar.update(**kwargs)

    def set_running(self, running: bool) -> None:
        """设置运行状态。"""
        self.is_running = running
        input_box = self.query_one("#input-box", InputBox)
        input_box.disabled = running
