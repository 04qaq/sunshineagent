"""Prompt 输入处理模块。

借鉴 opencode 的 prompt.shared.ts 设计：
- 历史记录导航（↑↓ 键）
- 多行输入支持
- 文件引用解析 (@filename)
- Agent 引用解析 (@agent)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PromptInput:
    """提示输入。"""
    text: str
    files: list[FileRef] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    mode: str | None = None  # "shell" 等


@dataclass
class FileRef:
    """文件引用。"""
    path: str
    start: int = 0
    end: int = 0


class PromptHistory:
    """提示历史记录管理。

    借鉴 opencode 的 PromptHistoryState 设计：
    - 环形缓冲区存储历史记录
    - 支持 ↑↓ 键导航
    - 保存草稿
    """

    def __init__(self, limit: int = 200):
        self._items: list[str] = []
        self._index: int | None = None
        self._draft: str = ""
        self._limit = limit

    @property
    def items(self) -> list[str]:
        """历史记录列表。"""
        return self._items.copy()

    @property
    def index(self) -> int | None:
        """当前浏览位置。"""
        return self._index

    @property
    def length(self) -> int:
        """历史记录长度。"""
        return len(self._items)

    def push(self, text: str):
        """添加到历史记录。"""
        text = text.strip()
        if not text:
            return

        # 避免连续重复
        if self._items and self._items[-1] == text:
            self._index = None
            self._draft = ""
            return

        self._items.append(text)

        # 限制长度
        if len(self._items) > self._limit:
            self._items = self._items[-self._limit:]

        self._index = None
        self._draft = ""

    def up(self, current_text: str) -> tuple[str, bool]:
        """↑ 键导航。

        Args:
            current_text: 当前输入框文本

        Returns:
            (新文本, 是否应用)
        """
        if not self._items:
            return current_text, False

        # 首次按下，保存草稿
        if self._index is None:
            self._draft = current_text
            self._index = len(self._items) - 1
        elif self._index > 0:
            self._index -= 1
        else:
            return current_text, False

        return self._items[self._index], True

    def down(self, current_text: str) -> tuple[str, bool]:
        """↓ 键导航。

        Args:
            current_text: 当前输入框文本

        Returns:
            (新文本, 是否应用)
        """
        if self._index is None:
            return current_text, False

        if self._index < len(self._items) - 1:
            self._index += 1
            return self._items[self._index], True
        else:
            # 恢复草稿
            self._index = None
            return self._draft, True

    def reset(self):
        """重置导航状态。"""
        self._index = None
        self._draft = ""


class PromptParser:
    """提示解析器。

    解析用户输入中的引用：
    - 文件引用: @filename, @path/to/file
    - Agent 引用: @agent_name
    """

    # 文件引用模式
    FILE_PATTERN = re.compile(r"@([\w./\\~-]+\.\w+)")

    # Agent 引用模式
    AGENT_PATTERN = re.compile(r"@(\w+)")

    # 已知 Agent 名称（需要动态更新）
    _known_agents: set[str] = set()

    @classmethod
    def set_known_agents(cls, agents: set[str]):
        """设置已知 Agent 名称。"""
        cls._known_agents = agents

    @classmethod
    def parse(cls, text: str, workspace: str = "") -> PromptInput:
        """解析用户输入。

        Args:
            text: 用户输入文本
            workspace: 工作区路径

        Returns:
            解析后的 PromptInput
        """
        files = []
        agents = []

        # 解析文件引用
        for match in cls.FILE_PATTERN.finditer(text):
            filename = match.group(1)
            filepath = cls._resolve_file(filename, workspace)
            if filepath:
                files.append(FileRef(
                    path=filepath,
                    start=match.start(),
                    end=match.end(),
                ))

        # 解析 Agent 引用
        for match in cls.AGENT_PATTERN.finditer(text):
            agent_name = match.group(1)
            if agent_name in cls._known_agents:
                agents.append(agent_name)

        return PromptInput(
            text=text,
            files=files,
            agents=agents,
        )

    @classmethod
    def _resolve_file(cls, filename: str, workspace: str) -> str | None:
        """解析文件路径。

        Args:
            filename: 文件名或相对路径
            workspace: 工作区路径

        Returns:
            解析后的绝对路径，或 None
        """
        if not workspace:
            return None

        # 处理 ~ 路径
        if filename.startswith("~/"):
            filepath = Path.home() / filename[2:]
        else:
            filepath = Path(workspace) / filename

        # 检查文件是否存在
        if filepath.exists():
            return str(filepath.resolve())

        # 尝试模糊匹配
        pattern = f"**/{filename}"
        matches = list(Path(workspace).glob(pattern))
        if matches:
            return str(matches[0].resolve())

        return None


def is_exit_command(text: str) -> bool:
    """是否是退出命令。"""
    return text.strip().lower() in ("/exit", "/quit", ":q")


def is_new_command(text: str) -> bool:
    """是否是新建会话命令。"""
    return text.strip().lower() in ("/new", "/clear")


def is_command(text: str) -> bool:
    """是否是命令（以 / 开头）。"""
    return text.strip().startswith("/")
