"""TaskGraph 引擎 —— DAG 数据结构 + 拓扑排序 + 层级并发执行。

每个节点表示一个子任务，由指定类型的 Worker 执行。
支持依赖解析、同层并发、失败重试。
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskNode:
    """DAG 中的一个任务节点。"""

    task_id: str
    task_type: str          # "explore" | "code" | "test" | "document" | "review"
    description: str        # 任务描述
    prompt: str             # 传给 Worker 的详细 prompt
    dependencies: list[str] = field(default_factory=list)  # 依赖的 task_id 列表
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    error: str | None = None
    model_id: str | None = None     # 路由后选中的模型
    provider_id: str | None = None
    retry_count: int = 0
    max_retries: int = 1


class TaskGraph:
    """有向无环任务图。"""

    def __init__(self):
        self._nodes: dict[str, TaskNode] = {}

    def add_node(self, node: TaskNode):
        self._nodes[node.task_id] = node

    def get(self, task_id: str) -> TaskNode | None:
        return self._nodes.get(task_id)

    @property
    def nodes(self) -> list[TaskNode]:
        return list(self._nodes.values())

    def topological_levels(self) -> list[list[TaskNode]]:
        """拓扑排序后按层级分组。

        第 0 层 = 无依赖的节点（可立即执行）
        第 1 层 = 依赖第 0 层的节点
        以此类推。

        Returns:
            [[level0_nodes], [level1_nodes], ...]
        """
        in_degree: dict[str, int] = {nid: 0 for nid in self._nodes}
        children: dict[str, list[str]] = {nid: [] for nid in self._nodes}

        for nid, node in self._nodes.items():
            for dep in node.dependencies:
                if dep in self._nodes:
                    in_degree[nid] += 1
                    children[dep].append(nid)

        levels: list[list[TaskNode]] = []
        queue: deque[str] = deque(
            nid for nid, deg in in_degree.items() if deg == 0
        )
        visited: set[str] = set()

        while queue:
            level: list[TaskNode] = []
            for _ in range(len(queue)):
                nid = queue.popleft()
                if nid in visited:
                    continue
                visited.add(nid)
                node = self._nodes[nid]
                if node.status != TaskStatus.SKIPPED:
                    level.append(node)
                for child in children.get(nid, []):
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        queue.append(child)
            if level:
                levels.append(level)

        return levels

    def all_completed(self) -> bool:
        """所有节点是否已完成或跳过。"""
        return all(
            n.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
            for n in self._nodes.values()
        )

    def summary(self) -> str:
        """生成执行摘要。"""
        lines = []
        for n in self._nodes.values():
            icon = {
                TaskStatus.COMPLETED: "✓",
                TaskStatus.FAILED: "✗",
                TaskStatus.RUNNING: "~",
                TaskStatus.SKIPPED: "-",
                TaskStatus.PENDING: "·",
            }.get(n.status, "?")
            model = f" ({n.model_id})" if n.model_id else ""
            lines.append(f"  [{icon}] {n.task_id}: {n.description}{model}")
        return "\n".join(lines)


class TaskGraphExecutor:
    """DAG 执行器 —— 层级并发执行，支持失败重试。"""

    def __init__(
        self,
        task_tool,
        router,
        max_concurrency: int = 4,
    ):
        self._task_tool = task_tool
        self._router = router
        self._max_concurrency = max_concurrency
        self._graph: TaskGraph | None = None

    async def execute(
        self,
        graph: TaskGraph,
        *,
        parent_session_id: str,
        on_progress=None,
    ) -> dict[str, TaskNode]:
        """按层级并发执行 DAG。

        Args:
            graph: 任务图
            parent_session_id: 父 session ID（用于创建子 session）
            on_progress: 进度回调(level_index, node, status)

        Returns:
            {task_id: TaskNode} 执行后的节点映射
        """
        self._graph = graph
        levels = graph.topological_levels()

        for level_idx, level in enumerate(levels):
            sem = asyncio.Semaphore(self._max_concurrency)

            async def _execute_node(node: TaskNode, _li=level_idx, _s=sem):
                async with _s:
                    node.status = TaskStatus.RUNNING
                    if on_progress:
                        on_progress(_li, node, "running")

                    # 路由选模型
                    primary, fallback = self._router.route_with_fallback(
                        node.task_type
                    )
                    node.model_id = primary.model_id
                    node.provider_id = primary.provider_id

                    try:
                        # 构建上游结果上下文
                        upstream = ""
                        if node.dependencies:
                            deps_result = []
                            for dep_id in node.dependencies:
                                dep_node = graph.get(dep_id)
                                if dep_node and dep_node.result:
                                    deps_result.append(
                                        f"[{dep_id} result]: {dep_node.result[:500]}"
                                    )
                            if deps_result:
                                upstream = (
                                    "Previous task results:\n"
                                    + "\n".join(deps_result)
                                    + "\n\n"
                                )

                        full_prompt = upstream + node.prompt

                        # 通过 TaskTool 执行
                        result = await self._task_tool.execute(
                            {
                                "description": f"{node.task_id}: {node.description}",
                                "prompt": full_prompt,
                                "subagent_type": node.task_type,
                                "model": node.model_id,
                            },
                            type("ToolContext", (), {
                                "session_id": parent_session_id,
                                "agent": "code",
                                "assistant_message_id": None,
                                "tool_call_id": node.task_id,
                            })(),
                        )

                        node.result = result.output
                        node.status = TaskStatus.COMPLETED

                    except Exception as e:
                        node.retry_count += 1
                        if node.retry_count <= node.max_retries:
                            node.status = TaskStatus.PENDING
                            if on_progress:
                                on_progress(_li, node, "retrying")
                            return

                        node.error = str(e)
                        node.status = TaskStatus.FAILED

                    if on_progress:
                        on_progress(_li, node, "completed")

            await asyncio.gather(
                *[_execute_node(n) for n in level]
            )

            # 将失败但可重试的节点重新入队
            retry_nodes = [
                n for n in level
                if n.status == TaskStatus.PENDING and n.retry_count <= n.max_retries
            ]
            if retry_nodes:
                levels.append(retry_nodes)

        return {nid: self._graph.get(nid) for nid in self._graph._nodes
                if self._graph.get(nid)}
