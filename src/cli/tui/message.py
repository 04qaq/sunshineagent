"""消息类型系统。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class ToolStatus(str, Enum):
    """工具执行状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class TextPart:
    """文本内容。"""
    type: str = "text"
    text: str = ""


@dataclass
class ToolPart:
    """工具调用。"""
    type: str = "tool"
    tool_name: str = ""
    tool_call_id: str = ""
    input: dict = field(default_factory=dict)
    status: ToolStatus = ToolStatus.PENDING
    output: str = ""
    error: str | None = None
    start_time: float = 0.0
    end_time: float | None = None


@dataclass
class ThinkingPart:
    """思考过程。"""
    type: str = "thinking"
    text: str = ""
    status: ToolStatus = ToolStatus.RUNNING
    start_time: float = 0.0
    end_time: float | None = None


@dataclass
class Message:
    """消息。"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    role: str = "user"
    agent: str = ""
    model: str = ""
    timestamp: float = field(default_factory=time.time)
    parts: list = field(default_factory=list)
    completed: bool = False
    duration: float | None = None


def create_user_message(text: str, agent: str = "") -> Message:
    """创建用户消息。"""
    return Message(
        role="user",
        agent=agent,
        parts=[TextPart(text=text)],
    )


def create_assistant_message(agent: str = "", model: str = "") -> Message:
    """创建助手消息。"""
    return Message(
        role="assistant",
        agent=agent,
        model=model,
    )


def create_system_message(text: str) -> Message:
    """创建系统消息。"""
    return Message(
        role="system",
        parts=[TextPart(text=text)],
    )
