"""Tests for Phase 3: Router, Workers, TaskGraph."""

from unittest.mock import patch


class TestCapabilityRouter:
    @patch("src.router.CapabilityRouter.route")
    def test_route_plan_task_mocked(self, mock_route):
        from src.provider.catalog import ModelEntry

        mock_route.return_value = ModelEntry(
            model_id="test/plan", provider_id="test",
            display_name="Plan Model", context_window=100000,
            max_output_tokens=8000, tags=["planning"], cost_tier="high",
        )
        from src.router import CapabilityRouter
        from src.provider.catalog import ModelCatalog

        catalog = ModelCatalog(".")
        router = CapabilityRouter(catalog)
        model = router.route("plan", max_cost="very-high")
        assert model is not None

    @patch("src.provider.catalog.load_sunshine_config")
    def test_route_explore_cheap(self, mock_load):
        from src.config.provider import ProviderConfig, ModelConfig

        mock_load.return_value = {
            "test": ProviderConfig(
                name="T", models={
                    "cheap": ModelConfig(name="C", cost="very-low",
                                         tags=["search"]),
                },
            ),
        }
        from src.router import CapabilityRouter
        from src.provider.catalog import ModelCatalog

        catalog = ModelCatalog(".")
        router = CapabilityRouter(catalog)
        model = router.route("explore", max_cost="medium")
        assert model is not None

    @patch("src.provider.catalog.load_sunshine_config")
    def test_cost_filter_blocks_expensive(self, mock_load):
        from src.config.provider import ProviderConfig, ModelConfig

        mock_load.return_value = {
            "test": ProviderConfig(
                name="T", models={
                    "expensive": ModelConfig(name="E", cost="very-high",
                                             tags=["planning", "review"]),
                },
            ),
        }
        from src.router import CapabilityRouter
        from src.provider.catalog import ModelCatalog

        catalog = ModelCatalog(".")
        router = CapabilityRouter(catalog)
        model = router.route("review", max_cost="medium")
        assert model is None  # 应被 cost 过滤掉

    @patch("src.provider.catalog.load_sunshine_config")
    def test_route_with_fallback(self, mock_load):
        from src.config.provider import ProviderConfig, ModelConfig

        mock_load.return_value = {
            "test": ProviderConfig(
                name="T", models={
                    "gen": ModelConfig(name="G", tags=["general"]),
                },
            ),
        }
        from src.router import CapabilityRouter
        from src.provider.catalog import ModelCatalog

        catalog = ModelCatalog(".")
        router = CapabilityRouter(catalog)
        primary, fallback = router.route_with_fallback("explore", max_cost="medium")
        assert primary is not None
        assert fallback is not None


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
