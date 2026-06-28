"""Tests for TaskGraph data structure."""

import pytest

from src.task_graph.graph import TaskGraph, TaskNode, TaskResult, TaskStatus


class TestTaskNode:
    def test_create_node(self):
        """测试创建节点。"""
        node = TaskNode(
            id="task_1",
            task_type="code",
            description="Test task",
            prompt="Do something",
        )
        assert node.id == "task_1"
        assert node.task_type == "code"
        assert node.status == TaskStatus.PENDING
        assert node.retry_count == 0
        assert node.max_retries == 3

    def test_mark_running(self):
        """测试标记为运行中。"""
        node = TaskNode(id="t1", task_type="code", description="", prompt="")
        node.mark_running()
        assert node.status == TaskStatus.RUNNING

    def test_mark_completed(self):
        """测试标记为完成。"""
        node = TaskNode(id="t1", task_type="code", description="", prompt="")
        result = TaskResult(success=True, output="Done")
        node.mark_completed(result)
        assert node.status == TaskStatus.COMPLETED
        assert node.result == result

    def test_mark_failed(self):
        """测试标记为失败。"""
        node = TaskNode(id="t1", task_type="code", description="", prompt="")
        node.mark_failed("Error occurred")
        assert node.status == TaskStatus.FAILED
        assert node.retry_count == 1
        assert "Error occurred" in node.error_history

    def test_can_retry(self):
        """测试是否可以重试。"""
        node = TaskNode(id="t1", task_type="code", description="", prompt="", max_retries=3)
        assert node.can_retry() is True

        node.retry_count = 2
        assert node.can_retry() is True

        node.retry_count = 3
        assert node.can_retry() is False

    def test_reset_for_retry(self):
        """测试重置状态以便重试。"""
        node = TaskNode(id="t1", task_type="code", description="", prompt="")
        node.mark_failed("Error")
        node.reset_for_retry()
        assert node.status == TaskStatus.PENDING
        assert node.result is None


class TestTaskGraph:
    def test_create_empty_graph(self):
        """测试创建空图。"""
        graph = TaskGraph()
        assert len(graph.nodes) == 0

    def test_add_node(self):
        """测试添加节点。"""
        graph = TaskGraph()
        node = TaskNode(id="t1", task_type="code", description="", prompt="")
        graph.add_node(node)
        assert "t1" in graph.nodes

    def test_topological_levels_simple(self):
        """测试简单拓扑排序。"""
        nodes = [
            TaskNode(id="a", task_type="code", description="", prompt=""),
            TaskNode(id="b", task_type="code", description="", prompt="", dependencies=["a"]),
            TaskNode(id="c", task_type="code", description="", prompt="", dependencies=["a"]),
            TaskNode(id="d", task_type="code", description="", prompt="", dependencies=["b", "c"]),
        ]
        graph = TaskGraph(nodes)
        levels = graph.topological_levels()

        assert len(levels) == 3
        assert levels[0] == ["a"]
        assert set(levels[1]) == {"b", "c"}
        assert levels[2] == ["d"]

    def test_topological_levels_parallel(self):
        """测试并行任务的拓扑排序。"""
        nodes = [
            TaskNode(id="a", task_type="code", description="", prompt=""),
            TaskNode(id="b", task_type="code", description="", prompt=""),
            TaskNode(id="c", task_type="code", description="", prompt="", dependencies=["a", "b"]),
        ]
        graph = TaskGraph(nodes)
        levels = graph.topological_levels()

        assert len(levels) == 2
        assert set(levels[0]) == {"a", "b"}
        assert levels[1] == ["c"]

    def test_get_ready_nodes(self):
        """测试获取可执行节点。"""
        a = TaskNode(id="a", task_type="code", description="", prompt="")
        b = TaskNode(id="b", task_type="code", description="", prompt="", dependencies=["a"])
        c = TaskNode(id="c", task_type="code", description="", prompt="", dependencies=["a"])

        graph = TaskGraph([a, b, c])

        # 初始状态，只有 a 可执行
        ready = graph.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].id == "a"

        # a 完成后，b 和 c 可执行
        a.mark_completed(TaskResult(success=True))
        ready = graph.get_ready_nodes()
        assert len(ready) == 2
        assert {n.id for n in ready} == {"b", "c"}

    def test_get_running_nodes(self):
        """测试获取运行中节点。"""
        a = TaskNode(id="a", task_type="code", description="", prompt="")
        b = TaskNode(id="b", task_type="code", description="", prompt="")
        graph = TaskGraph([a, b])

        a.mark_running()
        running = graph.get_running_nodes()
        assert len(running) == 1
        assert running[0].id == "a"

    def test_all_completed(self):
        """测试是否全部完成。"""
        a = TaskNode(id="a", task_type="code", description="", prompt="")
        b = TaskNode(id="b", task_type="code", description="", prompt="")
        graph = TaskGraph([a, b])

        assert graph.all_completed() is False

        a.mark_completed(TaskResult(success=True))
        assert graph.all_completed() is False

        b.mark_completed(TaskResult(success=True))
        assert graph.all_completed() is True

    def test_all_completed_with_failures(self):
        """测试包含失败的全部完成。"""
        a = TaskNode(id="a", task_type="code", description="", prompt="")
        b = TaskNode(id="b", task_type="code", description="", prompt="")
        graph = TaskGraph([a, b])

        a.mark_completed(TaskResult(success=True))
        b.mark_failed("Error")
        assert graph.all_completed() is True

    def test_has_failures(self):
        """测试是否有失败。"""
        a = TaskNode(id="a", task_type="code", description="", prompt="", max_retries=1)
        graph = TaskGraph([a])

        assert graph.has_failures() is False

        a.mark_failed("Error")
        assert graph.has_failures() is True  # retry_count=1, max_retries=1, 不能再重试

    def test_has_failures_can_retry(self):
        """测试有失败但可重试。"""
        a = TaskNode(id="a", task_type="code", description="", prompt="", max_retries=3)
        graph = TaskGraph([a])

        a.mark_failed("Error")  # retry_count=1
        assert graph.has_failures() is False  # 还可以重试

    def test_is_blocked(self):
        """测试是否被阻塞。"""
        a = TaskNode(id="a", task_type="code", description="", prompt="")
        b = TaskNode(id="b", task_type="code", description="", prompt="", dependencies=["a"])
        graph = TaskGraph([a, b])

        # a 运行中，b 被阻塞
        a.mark_running()
        assert graph.is_blocked() is True

        # a 完成后，不再阻塞
        a.mark_completed(TaskResult(success=True))
        assert graph.is_blocked() is False

    def test_from_json(self):
        """测试从 JSON 创建。"""
        data = {
            "nodes": [
                {
                    "id": "task_1",
                    "task_type": "code",
                    "description": "Test",
                    "prompt": "Do something",
                    "dependencies": [],
                    "relevant_files": ["test.py"],
                    "quality": "high",
                },
                {
                    "id": "task_2",
                    "task_type": "test",
                    "description": "Test 2",
                    "prompt": "Do something else",
                    "dependencies": ["task_1"],
                },
            ]
        }
        graph = TaskGraph.from_json(data)

        assert len(graph.nodes) == 2
        assert "task_1" in graph.nodes
        assert "task_2" in graph.nodes
        assert graph.nodes["task_1"].task_type == "code"
        assert graph.nodes["task_2"].dependencies == ["task_1"]

    def test_to_json(self):
        """测试转换为 JSON。"""
        nodes = [
            TaskNode(id="t1", task_type="code", description="Test", prompt=""),
        ]
        graph = TaskGraph(nodes)
        data = graph.to_json()

        assert "nodes" in data
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["id"] == "t1"

    def test_summary(self):
        """测试摘要。"""
        a = TaskNode(id="a", task_type="code", description="", prompt="")
        b = TaskNode(id="b", task_type="code", description="", prompt="")
        c = TaskNode(id="c", task_type="code", description="", prompt="")
        graph = TaskGraph([a, b, c])

        a.mark_completed(TaskResult(success=True))
        b.mark_running()

        summary = graph.summary()
        assert "3 个任务" in summary
        assert "完成: 1" in summary
        assert "运行中: 1" in summary
        assert "待执行: 1" in summary
