"""TUI 运行器。

连接 Textual App 和后端逻辑。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from src.cli.tui.app import SunshineApp


class TuiRunner:
    """TUI 运行器。

    职责：
    1. 创建和管理 TUI App
    2. 处理用户输入
    3. 调用后端逻辑
    4. 更新 UI 状态
    """

    def __init__(
        self,
        on_prompt: Callable[[str], Awaitable[None]],
        on_interrupt: Callable[[], None] | None = None,
        on_new_session: Callable[[], Awaitable[None]] | None = None,
        agent_name: str = "build",
        model_name: str = "",
    ):
        """
        Args:
            on_prompt: 提示处理函数
            on_interrupt: 中断处理函数
            on_new_session: 新会话处理函数
            agent_name: 初始 Agent 名称
            model_name: 初始模型名称
        """
        self._on_prompt = on_prompt
        self._on_interrupt = on_interrupt
        self._on_new_session = on_new_session
        self._agent_name = agent_name
        self._model_name = model_name

        self._app: SunshineApp | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run(self) -> None:
        """运行 TUI。"""
        self._app = SunshineApp(
            agent_name=self._agent_name,
            model_name=self._model_name,
        )

        # 注册消息处理器
        self._app.on(SunshineApp.PromptSubmitted, self._handle_prompt)
        self._app.on(SunshineApp.InterruptRequested, self._handle_interrupt)

        # 运行应用
        await self._app.run_async()

    async def _handle_prompt(self, event: SunshineApp.PromptSubmitted) -> None:
        """处理提示提交。"""
        text = event.text.strip()
        if not text:
            return

        # 显示用户消息
        self._app.add_message("user", text)

        # 设置运行状态
        self._app.set_running(True)
        self._app.update_status(running=True)

        try:
            # 调用后端处理
            await self._on_prompt(text)
        except Exception as e:
            # 显示错误
            self._app.add_message("error", str(e))
        finally:
            # 恢复状态
            self._app.set_running(False)
            self._app.update_status(running=False)

    def _handle_interrupt(self, event: SunshineApp.InterruptRequested) -> None:
        """处理中断请求。"""
        if self._on_interrupt:
            self._on_interrupt()

    def add_message(self, role: str, content: str) -> None:
        """添加消息到 UI。"""
        if self._app:
            self._app.add_message(role, content)

    def update_status(self, **kwargs: Any) -> None:
        """更新状态栏。"""
        if self._app:
            self._app.update_status(**kwargs)

    def update_agent(self, agent_name: str) -> None:
        """更新当前 Agent。"""
        self._agent_name = agent_name
        if self._app:
            self._app.current_agent = agent_name
            self._app.update_status(agent=agent_name)

    def update_model(self, model_name: str, provider: str = "") -> None:
        """更新当前模型。"""
        self._model_name = model_name
        if self._app:
            self._app.current_model = model_name
            self._app.update_status(model=model_name, provider=provider)
