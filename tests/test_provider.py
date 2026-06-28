"""Tests for provider system — ModelRouter routing logic."""

from unittest.mock import MagicMock

from src.provider.registry import ModelInfo, Provider
from src.provider.router import CAPABILITY_RANK, COST_RANK, ModelRouter


def _make_registry(providers: list[tuple[str, str, list[tuple[str, str, str, str]]]]):
    """Build a mock registry for testing.

    Each entry: (provider_id, protocol, [(model_id, name, cost, capability), ...])
    All providers have api_key set ('dummy').
    """
    registry = MagicMock()
    provider_dict: dict[str, Provider] = {}
    detected = []
    for pid, proto, models in providers:
        model_dict = {}
        for mid, name, cost, cap in models:
            model_dict[mid] = ModelInfo(provider=pid, model=mid, name=name,
                                        cost=cost, capability=cap)
        p = Provider(name=pid, protocol=proto, api_key="dummy",
                     models=model_dict)
        provider_dict[pid] = p
        detected.append(pid)
    registry.detected_providers = detected
    registry.get_provider = lambda pid, d=provider_dict: d.get(pid)
    return registry


class TestModelRouter:
    def test_select_explore_prefers_low_cost(self):
        registry = _make_registry([
            ("openai", "openai-compatible", [
                ("gpt-5", "GPT-5", "very-high", "very-high"),
            ]),
            ("deepseek", "openai-compatible", [
                ("deepseek-v4-flash", "DeepSeek Flash", "low", "medium"),
            ]),
        ])
        router = ModelRouter()
        result = router.select("explore", registry)
        assert result == ("deepseek", "deepseek-v4-flash")

    def test_select_general_requires_medium_capability(self):
        registry = _make_registry([
            ("qwen", "openai-compatible", [
                ("qwen3-8b", "Qwen3 8B", "very-low", "low"),
            ]),
            ("deepseek", "openai-compatible", [
                ("deepseek-v4-flash", "DeepSeek Flash", "low", "medium"),
            ]),
        ])
        router = ModelRouter()
        result = router.select("general", registry)
        assert result == ("deepseek", "deepseek-v4-flash")

    def test_select_excludes_insufficient_capability(self):
        registry = _make_registry([
            ("qwen", "openai-compatible", [
                ("qwen3-8b", "Qwen3 8B", "very-low", "low"),
            ]),
        ])
        router = ModelRouter()
        result = router.select("general", registry)
        assert result is None

    def test_select_respects_parent_cost(self):
        registry = _make_registry([
            ("openai", "openai-compatible", [
                ("gpt-5", "GPT-5", "very-high", "very-high"),
            ]),
            ("deepseek", "openai-compatible", [
                ("deepseek-v4-flash", "DeepSeek Flash", "low", "medium"),
                ("deepseek-v4-pro", "DeepSeek Pro", "high", "high"),
            ]),
        ])
        router = ModelRouter()
        result = router.select("general", registry, "deepseek", "deepseek-v4-flash")
        assert result == ("deepseek", "deepseek-v4-flash")

    def test_select_skips_unavailable(self):
        registry = _make_registry([
            ("deepseek", "openai-compatible", [
                ("deepseek-v4-flash", "DeepSeek Flash", "low", "medium"),
            ]),
            ("openai", "openai-compatible", [
                ("gpt-5-mini", "GPT-5 Mini", "medium", "medium"),
            ]),
        ])
        router = ModelRouter()
        router.mark_unavailable("deepseek", "deepseek-v4-flash")
        result = router.select("general", registry)
        assert result == ("openai", "gpt-5-mini")

    def test_select_none_when_all_unavailable(self):
        registry = _make_registry([
            ("deepseek", "openai-compatible", [
                ("deepseek-v4-flash", "DeepSeek Flash", "low", "medium"),
            ]),
        ])
        router = ModelRouter()
        router.mark_unavailable("deepseek", "deepseek-v4-flash")
        result = router.select("general", registry)
        assert result is None

    def test_unavailable_models_list(self):
        router = ModelRouter()
        router.mark_unavailable("deepseek", "v4")
        router.mark_unavailable("openai", "gpt-5")
        unavailable = router.unavailable_models
        assert "deepseek/v4" in unavailable
        assert "openai/gpt-5" in unavailable

    def test_cost_rank_order(self):
        assert COST_RANK["very-low"] < COST_RANK["low"] < COST_RANK["medium"] \
            < COST_RANK["high"] < COST_RANK["very-high"]

    def test_capability_rank_order(self):
        assert CAPABILITY_RANK["very-low"] < CAPABILITY_RANK["low"] \
            < CAPABILITY_RANK["medium"] < CAPABILITY_RANK["high"] \
            < CAPABILITY_RANK["very-high"]
