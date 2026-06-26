"""Tests for Phase 3: Router, Workers, TaskGraph."""



class TestCapabilityRouter:
    def test_route_plan_task(self):
        from src.router import CapabilityRouter

        router = CapabilityRouter()
        model = router.route("plan")
        assert model is not None
        assert "planning" in model.tags or "architecture" in model.tags

    def test_route_explore_cheap(self):
        from src.router import CapabilityRouter

        router = CapabilityRouter()
        model = router.route("explore")
        assert model is not None
        # explore 应该选便宜的
        assert model.input_price <= 5.0

    def test_route_review_strong(self):
        from src.router import CapabilityRouter

        router = CapabilityRouter()
        model = router.route("review")
        assert model is not None

    def test_route_with_provider_filter(self):
        from src.router import CapabilityRouter

        router = CapabilityRouter()
        model = router.route("code", provider="openai")
        if model:
            assert model.provider_id == "openai"


class TestWorkers:
    def test_all_worker_types(self):
        from src.agent.workers import WORKERS

        assert "explore" in WORKERS
        assert "code" in WORKERS
        assert "test" in WORKERS
        assert "document" in WORKERS
        assert "review" in WORKERS
        assert len(WORKERS) == 5

    def test_explore_is_readonly(self):
        from src.agent.workers import EXPLORE_WORKER

        assert EXPLORE_WORKER.permission.can_use("read")
        assert not EXPLORE_WORKER.permission.can_use("write")

    def test_code_has_full_access(self):
        from src.agent.workers import CODE_WORKER

        assert CODE_WORKER.permission.can_use("read")
        assert CODE_WORKER.permission.can_use("write")
        assert not CODE_WORKER.permission.can_use("task")


class TestTaskGraph:
    def test_topological_sort_simple(self):
        from src.task_graph import TaskGraph, TaskNode

        g = TaskGraph()
        g.add_node(TaskNode("a", "code", "task a", "do a", []))
        g.add_node(TaskNode("b", "code", "task b", "do b", ["a"]))
        g.add_node(TaskNode("c", "code", "task c", "do c", ["a"]))
        g.add_node(TaskNode("d", "code", "task d", "do d", ["b", "c"]))

        levels = g.topological_levels()
        assert len(levels) == 3
        assert {n.task_id for n in levels[0]} == {"a"}
        assert {n.task_id for n in levels[1]} == {"b", "c"}
        assert {n.task_id for n in levels[2]} == {"d"}

    def test_no_dependencies(self):
        from src.task_graph import TaskGraph, TaskNode

        g = TaskGraph()
        g.add_node(TaskNode("a", "code", "task a", "do a", []))
        g.add_node(TaskNode("b", "code", "task b", "do b", []))

        levels = g.topological_levels()
        assert len(levels) == 1
        assert len(levels[0]) == 2

    def test_summary(self):
        from src.task_graph import TaskGraph, TaskNode, TaskStatus

        g = TaskGraph()
        n = TaskNode("a", "code", "task a", "do a", [])
        n.status = TaskStatus.COMPLETED
        n.result = "done"
        g.add_node(n)

        s = g.summary()
        assert "✓" in s
        assert "task a" in s
