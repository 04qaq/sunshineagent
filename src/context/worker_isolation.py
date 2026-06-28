"""Worker Context Isolation - 逐字段决策的上下文隔离。

借鉴 Claude Code 的 createSubagentContext() 设计：
- 必须克隆：各用各的
- 子控制器：父死了子也得死
- No-Op 化：不能动父的状态
- 身份重建：你不是父 Agent
- 关键透传：必须共享的
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class WorkerIsolationConfig:
    """Worker 隔离配置。

    决定哪些上下文需要克隆、哪些需要透传、哪些需要置空。
    """
    # 必须克隆 - 各用各的
    clone_file_state: bool = True
    clone_read_cache: bool = True
    clone_tool_state: bool = True

    # 子控制器 - 父死了子也得死
    create_child_abort: bool = True

    # No-Op 化 - 不能动父的状态
    noop_set_state: bool = True
    noop_ui_callbacks: bool = True

    # 身份重建 - 你不是父 Agent
    new_agent_id: bool = True
    increment_depth: bool = True

    # 关键透传 - 必须共享的
    passthrough_task_registry: bool = True
    passthrough_session_hooks: bool = True


# 不同 Worker 类型的默认配置
WORKER_ISOLATION_CONFIGS: dict[str, WorkerIsolationConfig] = {
    "general": WorkerIsolationConfig(),
    "explore": WorkerIsolationConfig(
        clone_file_state=False,  # 只读，不需要克隆
        noop_set_state=True,
    ),
    "code": WorkerIsolationConfig(
        clone_file_state=True,
        clone_tool_state=True,
    ),
    "test": WorkerIsolationConfig(
        clone_file_state=True,
        clone_tool_state=True,
    ),
    "document": WorkerIsolationConfig(
        clone_file_state=False,  # 只读
        clone_tool_state=False,
    ),
}


class WorkerContext:
    """Worker 上下文 - 隔离后的执行环境。

    对应 Claude Code 的 ToolUseContext，但简化为 Python 实现。
    """

    def __init__(
        self,
        session_id: str,
        agent_name: str,
        agent_id: str,
        parent_session_id: str,
        parent_agent_id: str,
        depth: int,
        abort_signal: asyncio.Event | None = None,
        # 克隆的状态
        file_state: dict[str, Any] | None = None,
        read_cache: dict[str, Any] | None = None,
        tool_state: dict[str, Any] | None = None,
        # 透传的引用
        task_registry: Any = None,
        session_hooks: Any = None,
        # No-Op 的回调
        set_state: Callable | None = None,
        ui_callbacks: dict[str, Callable] | None = None,
    ):
        self.session_id = session_id
        self.agent_name = agent_name
        self.agent_id = agent_id
        self.parent_session_id = parent_session_id
        self.parent_agent_id = parent_agent_id
        self.depth = depth
        self.abort_signal = abort_signal
        self.file_state = file_state or {}
        self.read_cache = read_cache or {}
        self.tool_state = tool_state or {}
        self.task_registry = task_registry
        self.session_hooks = session_hooks
        self.set_state = set_state or (lambda: None)
        self.ui_callbacks = ui_callbacks or {}

    def is_root(self) -> bool:
        """是否是根 Agent。"""
        return self.depth == 0

    def can_create_subagent(self) -> bool:
        """是否可以创建子 Agent。"""
        return self.depth < 2  # 最多 2 层

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于持久化）。"""
        return {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "agent_id": self.agent_id,
            "parent_session_id": self.parent_session_id,
            "parent_agent_id": self.parent_agent_id,
            "depth": self.depth,
        }


class WorkerContextFactory:
    """Worker 上下文工厂 - 创建隔离的 Worker 执行环境。

    对应 Claude Code 的 createSubagentContext()。
    """

    def __init__(self, isolation_configs: dict[str, WorkerIsolationConfig] | None = None):
        self._configs = isolation_configs or WORKER_ISOLATION_CONFIGS
        self._agent_counter = 0

    def create(
        self,
        parent_context: WorkerContext | None,
        agent_name: str,
        session_id: str,
        config: WorkerIsolationConfig | None = None,
    ) -> WorkerContext:
        """创建 Worker 上下文。

        Args:
            parent_context: 父 Agent 的上下文（None 表示根 Agent）
            agent_name: Worker 类型名称
            session_id: Worker 的 Session ID
            config: 隔离配置（可选，使用默认配置）

        Returns:
            隔离后的 Worker 上下文
        """
        if config is None:
            config = self._configs.get(agent_name, WorkerIsolationConfig())

        # 生成新的 Agent ID
        self._agent_counter += 1
        agent_id = f"agent-{self._agent_counter:06d}"

        if parent_context is None:
            # 根 Agent - 不隔离
            return WorkerContext(
                session_id=session_id,
                agent_name=agent_name,
                agent_id=agent_id,
                parent_session_id="",
                parent_agent_id="",
                depth=0,
            )

        # 子 Agent - 逐字段隔离
        return self._isolate(parent_context, agent_name, session_id, agent_id, config)

    def _isolate(
        self,
        parent: WorkerContext,
        agent_name: str,
        session_id: str,
        agent_id: str,
        config: WorkerIsolationConfig,
    ) -> WorkerContext:
        """执行逐字段隔离。"""

        # 必须克隆 - 各用各的
        file_state = self._clone_if(parent.file_state, config.clone_file_state)
        read_cache = self._clone_if(parent.read_cache, config.clone_read_cache)
        tool_state = self._clone_if(parent.tool_state, config.clone_tool_state)

        # 子控制器 - 父死了子也得死
        abort_signal = None
        if config.create_child_abort and parent.abort_signal:
            abort_signal = self._create_child_abort(parent.abort_signal)

        # No-Op 化 - 不能动父的状态
        set_state = self._noop if config.noop_set_state else parent.set_state
        ui_callbacks = self._noop_dict if config.noop_ui_callbacks else parent.ui_callbacks

        # 身份重建 - 你不是父 Agent
        depth = parent.depth + 1 if config.increment_depth else parent.depth

        # 关键透传 - 必须共享的
        task_registry = parent.task_registry if config.passthrough_task_registry else None
        session_hooks = parent.session_hooks if config.passthrough_session_hooks else None

        return WorkerContext(
            session_id=session_id,
            agent_name=agent_name,
            agent_id=agent_id,
            parent_session_id=parent.session_id,
            parent_agent_id=parent.agent_id,
            depth=depth,
            abort_signal=abort_signal,
            file_state=file_state,
            read_cache=read_cache,
            tool_state=tool_state,
            task_registry=task_registry,
            session_hooks=session_hooks,
            set_state=set_state,
            ui_callbacks=ui_callbacks,
        )

    def _clone_if(self, data: Any, should_clone: bool) -> Any:
        """条件克隆。"""
        if not should_clone:
            return {}
        if isinstance(data, dict):
            return data.copy()
        if isinstance(data, set):
            return data.copy()
        if isinstance(data, list):
            return data.copy()
        return data

    def _create_child_abort(self, parent_abort: asyncio.Event) -> asyncio.Event:
        """创建子控制器 - 父 Ctrl+C → 子自动取消。"""
        child_abort = asyncio.Event()

        async def _watch_parent():
            await parent_abort.wait()
            child_abort.set()

        # 在后台监听父的取消信号
        asyncio.create_task(_watch_parent())

        return child_abort

    @staticmethod
    def _noop(*args, **kwargs):
        """No-Op 函数。"""
        pass

    @staticmethod
    def _noop_dict(*args, **kwargs):
        """No-Op 字典。"""
        return {}
