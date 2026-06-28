"""TaskGraph - 任务依赖图数据结构。

用于 Executive 模式的任务规划和调度：
- TaskNode: 单个任务节点
- TaskResult: 任务执行结果
- TaskGraph: 任务依赖图，支持拓扑排序和状态管理
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskResult:
    """任务执行结果。"""
    success: bool
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskNode:
    """任务节点。"""
    id: str
    task_type: str  # search/code/test/document/general
    description: str
    prompt: str
    dependencies: list[str] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)
    quality: str = "medium"  # low/medium/high
    status: TaskStatus = TaskStatus.PENDING
    result: TaskResult | None = None
    retry_count: int = 0
    max_retries: int = 3
    error_history: list[str] = field(default_factory=list)

    def can_retry(self) -> bool:
        """是否可以重试。"""
        return self.retry_count < self.max_retries

    def mark_running(self):
        """标记为运行中。"""
        self.status = TaskStatus.RUNNING

    def mark_completed(self, result: TaskResult):
        """标记为完成。"""
        self.status = TaskStatus.COMPLETED
        self.result = result

    def mark_failed(self, error: str):
        """标记为失败。"""
        self.status = TaskStatus.FAILED
        self.error_history.append(error)
        self.retry_count += 1

    def reset_for_retry(self):
        """重置状态以便重试。"""
        self.status = TaskStatus.PENDING
        self.result = None


class TaskGraph:
    """任务依赖图。

    支持：
    - 拓扑排序（分层）
    - 获取可执行节点
    - 状态管理
    """

    def __init__(self, nodes: list[TaskNode] | None = None):
        self._nodes: dict[str, TaskNode] = {}
        if nodes:
            for node in nodes:
                self._nodes[node.id] = node

    @property
    def nodes(self) -> dict[str, TaskNode]:
        """所有节点。"""
        return self._nodes

    def add_node(self, node: TaskNode):
        """添加节点。"""
        self._nodes[node.id] = node

    def get_node(self, node_id: str) -> TaskNode | None:
        """获取节点。"""
        return self._nodes.get(node_id)

    def topological_levels(self) -> list[list[str]]:
        """拓扑排序，返回分层结果。

        Returns:
            分层列表，每层包含可并行执行的节点 ID。
            Level 0: 无依赖的节点
            Level 1: 依赖 Level 0 的节点
            ...
        """
        if not self._nodes:
            return []

        # 计算入度
        in_degree: dict[str, int] = {nid: 0 for nid in self._nodes}
        for node in self._nodes.values():
            for dep in node.dependencies:
                if dep in self._nodes:
                    in_degree[node.id] += 1

        # BFS 拓扑排序
        levels: list[list[str]] = []
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])

        while queue:
            level = list(queue)
            levels.append(level)
            next_queue = deque()

            for nid in level:
                # 找到依赖当前节点的后续节点
                for node in self._nodes.values():
                    if nid in node.dependencies:
                        in_degree[node.id] -= 1
                        if in_degree[node.id] == 0:
                            next_queue.append(node.id)

            queue = next_queue

        return levels

    def get_ready_nodes(self) -> list[TaskNode]:
        """获取可执行节点（依赖已完成，自身待执行）。

        Returns:
            可执行的节点列表
        """
        ready = []
        for node in self._nodes.values():
            if node.status != TaskStatus.PENDING:
                continue

            # 检查所有依赖是否完成
            all_deps_completed = all(
                self._nodes.get(dep) is not None
                and self._nodes[dep].status == TaskStatus.COMPLETED
                for dep in node.dependencies
            )

            if all_deps_completed:
                ready.append(node)

        return ready

    def get_running_nodes(self) -> list[TaskNode]:
        """获取正在执行的节点。"""
        return [n for n in self._nodes.values() if n.status == TaskStatus.RUNNING]

    def get_completed_nodes(self) -> list[TaskNode]:
        """获取已完成的节点。"""
        return [n for n in self._nodes.values() if n.status == TaskStatus.COMPLETED]

    def get_failed_nodes(self) -> list[TaskNode]:
        """获取失败的节点。"""
        return [n for n in self._nodes.values() if n.status == TaskStatus.FAILED]

    def all_completed(self) -> bool:
        """是否全部完成（或完成+失败）。"""
        return all(
            n.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for n in self._nodes.values()
        )

    def has_failures(self) -> bool:
        """是否有失败且无法重试的节点。"""
        return any(
            n.status == TaskStatus.FAILED and not n.can_retry()
            for n in self._nodes.values()
        )

    def is_blocked(self) -> bool:
        """是否被阻塞（无可执行节点，有运行中节点）。"""
        return len(self.get_ready_nodes()) == 0 and len(self.get_running_nodes()) > 0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TaskGraph:
        """从 JSON 创建 TaskGraph。

        Args:
            data: 包含 nodes 列表的字典

        Returns:
            TaskGraph 实例
        """
        graph = cls()
        for node_data in data.get("nodes", []):
            node = TaskNode(
                id=node_data["id"],
                task_type=node_data.get("task_type", "general"),
                description=node_data.get("description", ""),
                prompt=node_data.get("prompt", ""),
                dependencies=node_data.get("dependencies", []),
                relevant_files=node_data.get("relevant_files", []),
                quality=node_data.get("quality", "medium"),
                max_retries=node_data.get("max_retries", 3),
            )
            graph.add_node(node)
        return graph

    def to_json(self) -> dict[str, Any]:
        """转换为 JSON。"""
        return {
            "nodes": [
                {
                    "id": node.id,
                    "task_type": node.task_type,
                    "description": node.description,
                    "prompt": node.prompt,
                    "dependencies": node.dependencies,
                    "relevant_files": node.relevant_files,
                    "quality": node.quality,
                    "status": node.status.value,
                    "retry_count": node.retry_count,
                }
                for node in self._nodes.values()
            ]
        }

    def summary(self) -> str:
        """生成摘要。"""
        total = len(self._nodes)
        completed = len(self.get_completed_nodes())
        failed = len(self.get_failed_nodes())
        running = len(self.get_running_nodes())
        pending = total - completed - failed - running

        lines = [f"任务图: {total} 个任务"]
        if completed:
            lines.append(f"  完成: {completed}")
        if running:
            lines.append(f"  运行中: {running}")
        if pending:
            lines.append(f"  待执行: {pending}")
        if failed:
            lines.append(f"  失败: {failed}")

        return "\n".join(lines)
