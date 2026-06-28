"""TUI 状态栏组件。

显示当前执行状态：Agent、模型、时长、Token 使用量。
"""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static


class TuiStatusBar(Static):
    """状态栏组件。

    借鉴 opencode 的 FooterState 设计：
    - 显示当前 Agent
    - 显示当前模型
    - 显示执行时长
    - 显示 Token 使用量
    - 显示运行状态
    """

    # 响应式状态
    agent: reactive[str] = reactive("build")
    model: reactive[str] = reactive("")
    provider: reactive[str] = reactive("")
    duration: reactive[str] = reactive("")
    tokens: reactive[str] = reactive("")
    is_running: reactive[bool] = reactive(False)
    queue_count: reactive[int] = reactive(0)

    def render(self) -> str:
        """渲染状态栏。"""
        parts = []

        # Agent
        parts.append(f"[bold cyan]{self.agent}[/bold cyan]")

        # Model
        if self.model:
            model_display = self.model
            if self.provider:
                model_display = f"{self.provider}/{self.model}"
            parts.append(f"[white]{model_display}[/white]")

        # 运行状态
        if self.is_running:
            parts.append("[yellow]⟳ 运行中[/yellow]")
        else:
            parts.append("[dim]● 就绪[/dim]")

        # Duration
        if self.duration:
            parts.append(f"[dim]{self.duration}[/dim]")

        # Tokens
        if self.tokens:
            parts.append(f"[dim]{self.tokens}[/dim]")

        # Queue
        if self.queue_count > 0:
            parts.append(f"[dim]队列:{self.queue_count}[/dim]")

        return " │ ".join(parts)

    def update(
        self,
        agent: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        duration: str | None = None,
        tokens: str | None = None,
        running: bool | None = None,
        queue_count: int | None = None,
    ) -> None:
        """更新状态栏。"""
        if agent is not None:
            self.agent = agent
        if model is not None:
            self.model = model
        if provider is not None:
            self.provider = provider
        if duration is not None:
            self.duration = duration
        if tokens is not None:
            self.tokens = tokens
        if running is not None:
            self.is_running = running
        if queue_count is not None:
            self.queue_count = queue_count
