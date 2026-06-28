"""Prompt Queue 模块。

借鉴 opencode 的 runtime.queue.ts 设计：
- 串行执行队列
- 支持中断
- 支持 /new 命令
- 追踪每轮执行时长
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.cli.prompt import PromptInput, is_exit_command, is_new_command


@dataclass
class QueuedPrompt:
    """队列中的提示。"""
    input: PromptInput
    message_id: str = ""
    part_id: str = ""


class PromptQueue:
    """提示队列。

    借鉴 opencode 的 runPromptQueue 设计：
    - 串行执行提示
    - 支持中断当前执行
    - 支持 /new 命令创建新会话
    - 追踪执行状态
    """

    def __init__(
        self,
        run_fn: Callable[[PromptInput, asyncio.Event], Awaitable[None]],
        on_new_session: Callable[[], Awaitable[None]] | None = None,
        on_send: Callable[[PromptInput], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ):
        """
        Args:
            run_fn: 执行函数 (prompt, abort_signal) -> None
            on_new_session: 新建会话回调
            on_send: 发送提示回调
            on_status: 状态更新回调
        """
        self._run_fn = run_fn
        self._on_new_session = on_new_session
        self._on_send = on_send
        self._on_status = on_status or (lambda x: None)

        self._queue: list[QueuedPrompt] = []
        self._active: QueuedPrompt | None = None
        self._abort: asyncio.Event = asyncio.Event()
        self._closed: bool = False
        self._draining: bool = False

        # 执行历史
        self._history: list[dict[str, Any]] = []

    @property
    def queue_length(self) -> int:
        """队列长度。"""
        return len(self._queue)

    @property
    def is_running(self) -> bool:
        """是否正在执行。"""
        return self._active is not None

    @property
    def history(self) -> list[dict[str, Any]]:
        """执行历史。"""
        return self._history.copy()

    def submit(self, prompt: PromptInput, message_id: str = ""):
        """提交提示到队列。

        Args:
            prompt: 提示输入
            message_id: 消息 ID（可选）
        """
        if self._closed:
            return

        queued = QueuedPrompt(
            input=prompt,
            message_id=message_id,
        )
        self._queue.append(queued)

        if self._on_send:
            self._on_send(prompt)

        # 尝试开始执行
        self._try_drain()

    def interrupt(self):
        """中断当前执行。"""
        if self._active:
            self._abort.set()

    def close(self):
        """关闭队列。"""
        self._closed = True
        self._queue.clear()
        if self._active:
            self._abort.set()

    async def wait(self):
        """等待队列执行完成。"""
        while self._active or self._queue:
            await asyncio.sleep(0.1)

    def _try_drain(self):
        """尝试开始执行队列。"""
        if self._draining or self._closed or not self._queue:
            return

        self._draining = True
        asyncio.create_task(self._drain())

    async def _drain(self):
        """执行队列中的提示。"""
        try:
            while not self._closed and self._queue:
                queued = self._queue.pop(0)
                self._active = queued

                # 检查命令
                text = queued.input.text.strip()

                # /exit 命令
                if is_exit_command(text):
                    self.close()
                    return

                # /new 命令
                if is_new_command(text):
                    if self._on_new_session:
                        await self._on_new_session()
                    self._active = None
                    continue

                # 执行提示
                self._abort.clear()
                self._on_status("running")

                import time
                start_time = time.time()

                try:
                    await self._run_fn(queued.input, self._abort)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    self._on_status(f"error: {e}")

                elapsed = time.time() - start_time

                # 记录历史
                self._history.append({
                    "text": queued.input.text[:100],
                    "duration": elapsed,
                    "aborted": self._abort.is_set(),
                })

                self._active = None
                self._on_status("idle")
        finally:
            self._draining = False
