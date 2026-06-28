"""状态栏模块。

借鉴 opencode 的 FooterState 设计：
- 显示当前 Agent
- 显示当前模型
- 显示执行时长
- 显示 Token 使用量
- 显示队列状态
- 显示中断状态
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class UsageStats:
    """Token 使用统计。"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, other: UsageStats):
        """累加使用量。"""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write

    def format(self) -> str:
        """格式化显示。"""
        if self.total_tokens == 0:
            return ""

        # 格式化 token 数量
        if self.total_tokens >= 1000000:
            token_str = f"{self.total_tokens / 1000000:.1f}M"
        elif self.total_tokens >= 1000:
            token_str = f"{self.total_tokens / 1000:.1f}K"
        else:
            token_str = str(self.total_tokens)

        return f"{token_str} tokens"


@dataclass
class StatusBar:
    """状态栏。

    借鉴 opencode 的 FooterState：
    - phase: idle/running
    - agent: 当前 Agent 名称
    - model: 当前模型名称
    - duration: 执行时长
    - usage: Token 使用量
    - queue: 队列长度
    - interrupt: 中断次数
    """

    # 当前状态
    phase: str = "idle"  # "idle" | "running"
    agent: str = "build"
    model: str = ""
    provider: str = ""

    # 执行信息
    start_time: float | None = None
    duration: str = ""

    # 使用统计
    usage: UsageStats = field(default_factory=UsageStats)

    # 队列状态
    queue: int = 0

    # 中断状态
    interrupt_count: int = 0

    # 会话信息
    session_id: str = ""

    def start(self):
        """开始计时。"""
        self.phase = "running"
        self.start_time = time.time()
        self.usage = UsageStats()

    def stop(self):
        """停止计时。"""
        self.phase = "idle"
        if self.start_time:
            elapsed = time.time() - self.start_time
            self.duration = self._format_duration(elapsed)
        self.start_time = None

    def update_usage(self, usage: UsageStats):
        """更新使用量。"""
        self.usage.add(usage)

    def increment_interrupt(self):
        """增加中断计数。"""
        self.interrupt_count += 1

    def reset_interrupt(self):
        """重置中断计数。"""
        self.interrupt_count = 0

    def render(self, compact: bool = False) -> str:
        """渲染状态栏。

        Args:
            compact: 是否紧凑模式

        Returns:
            状态栏文本
        """
        parts = []

        # Agent
        parts.append(self.agent)

        # Model
        if self.model:
            model_display = self.model
            if self.provider:
                model_display = f"{self.provider}/{self.model}"
            parts.append(model_display)

        # Duration (实时更新)
        if self.phase == "running" and self.start_time:
            elapsed = time.time() - self.start_time
            parts.append(self._format_duration(elapsed))
        elif self.duration:
            parts.append(self.duration)

        # Usage
        usage_str = self.usage.format()
        if usage_str:
            parts.append(usage_str)

        # Queue
        if self.queue > 0:
            parts.append(f"queue:{self.queue}")

        # Interrupt
        if self.interrupt_count > 0:
            parts.append(f"^C {self.interrupt_count}x")

        # Session
        if not compact and self.session_id:
            parts.append(f"[{self.session_id[:8]}]")

        return " │ ".join(parts)

    def _format_duration(self, seconds: float) -> str:
        """格式化时长。"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"


@dataclass
class TurnSummary:
    """单轮执行摘要。"""
    agent: str
    model: str
    duration: float
    usage: UsageStats
    timestamp: datetime = field(default_factory=datetime.now)

    def format(self) -> str:
        """格式化显示。"""
        duration_str = self._format_duration(self.duration)
        usage_str = self.usage.format()
        return f"[{self.agent}] {self.model} - {duration_str} - {usage_str}"

    def _format_duration(self, seconds: float) -> str:
        """格式化时长。"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
