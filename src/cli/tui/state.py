"""状态管理器。"""

from __future__ import annotations

import time
from collections.abc import Callable

from src.cli.tui.message import (
    Message,
    ToolPart,
    ToolStatus,
    create_assistant_message,
    create_system_message,
    create_user_message,
)


class StateManager:
    """状态管理器。"""

    def __init__(self):
        self._messages: list[Message] = []
        self._active_tools: dict[str, ToolPart] = {}
        self._update_callbacks: list[Callable] = []

        # 会话状态
        self.session_id: str = ""
        self.agent: str = "build"
        self.model: str = ""
        self.provider: str = ""
        self.phase: str = "idle"
        self.start_time: float | None = None
        self.duration: str = ""

        # Context Panel 数据
        self.project_name: str = "SunshineAgent"
        self.tokens_used: int = 0
        self.tokens_limit: int = 200000
        self.cost: float = 0.0
        self.git_branch: str = ""
        self.git_modified: int = 0
        self.lsp_enabled: bool = False
        self.modified_files: list[dict] = []

    @property
    def messages(self) -> list[Message]:
        return self._messages.copy()

    @property
    def active_tools(self) -> list[ToolPart]:
        return [t for t in self._active_tools.values() if t.status == ToolStatus.RUNNING]

    def on_update(self, callback: Callable) -> None:
        """注册更新回调。"""
        self._update_callbacks.append(callback)

    def notify_update(self) -> None:
        """通知更新。"""
        for cb in self._update_callbacks:
            try:
                cb()
            except Exception:
                pass

    # ── 消息管理 ──────────────────────────────────────────────────────

    def add_user_message(self, text: str, agent: str = "") -> Message:
        """添加用户消息。"""
        msg = create_user_message(text, agent or self.agent)
        self._messages.append(msg)
        self.notify_update()
        return msg

    def add_assistant_message(self) -> Message:
        """添加助手消息。"""
        msg = create_assistant_message(self.agent, self.model)
        self._messages.append(msg)
        self.notify_update()
        return msg

    def add_system_message(self, text: str) -> Message:
        """添加系统消息。"""
        msg = create_system_message(text)
        self._messages.append(msg)
        self.notify_update()
        return msg

    def get_last_assistant(self) -> Message | None:
        """获取最后一条助手消息。"""
        for msg in reversed(self._messages):
            if msg.role == "assistant":
                return msg
        return None

    # ── 工具管理 ──────────────────────────────────────────────────────

    def tool_start(self, tool_call_id: str, tool_name: str, input_params: dict) -> ToolPart:
        """工具开始执行。"""
        tool = ToolPart(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            input=input_params,
            status=ToolStatus.RUNNING,
            start_time=time.time(),
        )
        self._active_tools[tool_call_id] = tool

        # 添加到助手消息
        last = self.get_last_assistant()
        if last:
            last.parts.append(tool)

        self.notify_update()
        return tool

    def tool_output(self, tool_call_id: str, output: str) -> None:
        """更新工具输出。"""
        tool = self._active_tools.get(tool_call_id)
        if tool:
            tool.output = output
            self.notify_update()

    def tool_complete(self, tool_call_id: str, output: str) -> None:
        """工具执行完成。"""
        tool = self._active_tools.get(tool_call_id)
        if tool:
            tool.status = ToolStatus.COMPLETED
            tool.output = output
            tool.end_time = time.time()
            self._active_tools.pop(tool_call_id, None)
            self.notify_update()

    def tool_error(self, tool_call_id: str, error: str) -> None:
        """工具执行失败。"""
        tool = self._active_tools.get(tool_call_id)
        if tool:
            tool.status = ToolStatus.ERROR
            tool.error = error
            tool.end_time = time.time()
            self._active_tools.pop(tool_call_id, None)
            self.notify_update()

    # ── 会话状态 ──────────────────────────────────────────────────────

    def set_running(self) -> None:
        """设置为运行状态。"""
        self.phase = "running"
        self.start_time = time.time()
        self.notify_update()

    def set_idle(self) -> None:
        """设置为空闲状态。"""
        self.phase = "idle"
        if self.start_time:
            elapsed = time.time() - self.start_time
            self.duration = self._format_duration(elapsed)
        self.start_time = None
        self.notify_update()

    def clear(self) -> None:
        """清空状态。"""
        self._messages.clear()
        self._active_tools.clear()
        self.phase = "idle"
        self.start_time = None
        self.duration = ""
        self.notify_update()

    def _format_duration(self, seconds: float) -> str:
        """格式化时长。"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            m = int(seconds // 60)
            s = int(seconds % 60)
            return f"{m}m {s}s"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h}h {m}m"
