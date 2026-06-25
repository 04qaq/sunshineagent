"""Tests for the provider layer."""


from src.provider.catalog import ModelCatalog


class TestModelCatalog:
    def test_resolve_valid(self):
        catalog = ModelCatalog()
        entry = catalog.resolve("anthropic", "claude-sonnet-4-6")
        assert entry is not None
        assert entry.model_id == "claude-sonnet-4-6"

    def test_resolve_invalid(self):
        catalog = ModelCatalog()
        entry = catalog.resolve("nonexistent", "model")
        assert entry is None

    def test_list_by_tag(self):
        catalog = ModelCatalog()
        planning = catalog.list_by_tag("planning")
        assert len(planning) > 0
        for m in planning:
            assert "planning" in m.tags

    def test_list_by_provider(self):
        catalog = ModelCatalog()
        anthropic = catalog.list_by_provider("anthropic")
        assert len(anthropic) > 0
        for m in anthropic:
            assert m.provider_id == "anthropic"
