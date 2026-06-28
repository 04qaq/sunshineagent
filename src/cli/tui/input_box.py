"""TUI 输入框组件。

支持多行输入、历史导航、快捷键。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, Static


class InputBox(Horizontal):
    """输入框组件。

    借鉴 opencode 的输入框设计：
    - 支持多行输入
    - 支持历史导航（↑↓ 键）
    - 支持快捷键
    - 显示提示符
    """

    class Submitted(Message):
        """输入提交消息。"""
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class Changed(Message):
        """输入变化消息。"""
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    # 响应式状态
    prompt_text: reactive[str] = reactive("> ")
    disabled: reactive[bool] = reactive(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._input: Input | None = None

    def compose(self) -> ComposeResult:
        """构建输入框布局。"""
        yield Static(self.prompt_text, id="prompt-label")
        yield Input(
            placeholder="输入提示...",
            id="prompt-input",
        )

    def on_mount(self) -> None:
        """挂载后初始化。"""
        self._input = self.query_one("#prompt-input", Input)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """处理输入提交。"""
        event.stop()
        self.post_message(self.Submitted(event.value))

    def on_input_changed(self, event: Input.Changed) -> None:
        """处理输入变化。"""
        self.post_message(self.Changed(event.value))

    def watch_disabled(self, disabled: bool) -> None:
        """监听禁用状态变化。"""
        if self._input:
            self._input.disabled = disabled

    def watch_prompt_text(self, prompt: str) -> None:
        """监听提示文本变化。"""
        try:
            label = self.query_one("#prompt-label", Static)
            label.update(prompt)
        except Exception:
            pass

    @property
    def value(self) -> str:
        """获取输入值。"""
        return self._input.value if self._input else ""

    @value.setter
    def value(self, text: str) -> None:
        """设置输入值。"""
        if self._input:
            self._input.value = text

    def focus(self) -> None:
        """聚焦输入框。"""
        if self._input:
            self._input.focus()
