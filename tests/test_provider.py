"""Tests for provider model system."""

from unittest.mock import patch

from src.provider.catalog import ModelCatalog


class TestModelCatalog:
    @patch("src.provider.catalog.load_sunshine_config")
    def test_resolve_valid(self, mock_load):
        from src.config.provider import ProviderConfig, ModelConfig

        mock_load.return_value = {
            "anthropic": ProviderConfig(
                name="Anthropic",
                models={"claude-sonnet": ModelConfig(name="CS", cost="high")},
            ),
        }
        catalog = ModelCatalog(".")
        entry = catalog.resolve("anthropic", "claude-sonnet")
        assert entry is not None
        assert entry.display_name == "CS"

    @patch("src.provider.catalog.load_sunshine_config")
    def test_list_by_tag(self, mock_load):
        from src.config.provider import ProviderConfig, ModelConfig

        mock_load.return_value = {
            "test": ProviderConfig(
                name="Test",
                models={"m1": ModelConfig(name="M1", tags=["planning", "code"])},
            ),
        }
        catalog = ModelCatalog(".")
        planning = catalog.list_by_tag("planning")
        assert len(planning) > 0

    @patch("src.provider.catalog.load_sunshine_config")
    def test_list_by_cost(self, mock_load):
        from src.config.provider import ProviderConfig, ModelConfig

        mock_load.return_value = {
            "test": ProviderConfig(
                name="Test",
                models={
                    "m1": ModelConfig(name="M1", cost="low"),
                    "m2": ModelConfig(name="M2", cost="very-high"),
                },
            ),
        }
        catalog = ModelCatalog(".")
        cheap = catalog.list_by_cost("medium")
        assert len(cheap) == 1
        assert cheap[0].model_id == "test/m1"

    @patch("src.provider.catalog.load_sunshine_config")
    def test_resolve_fuzzy(self, mock_load):
        from src.config.provider import ProviderConfig, ModelConfig

        mock_load.return_value = {
            "deepseek": ProviderConfig(
                name="DS",
                models={"v4-flash": ModelConfig(name="V4 Flash", cost="low")},
            ),
        }
        catalog = ModelCatalog(".")
        m = catalog.resolve_fuzzy("v4-flash")
        assert m is not None
        assert m.provider_id == "deepseek"
