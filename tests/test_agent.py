"""Tests for the agent system."""

import pytest

from src.agent.builtins import BUILTIN_AGENTS, AgentRegistry
from src.agent.permissions import PermissionRuleset
from src.models.database import Database


@pytest.fixture
async def db():
    d = Database(db_path=":memory:")
    await d.init()
    yield d
    await d.close()


class TestPermissionRuleset:
    def test_all_permissions(self):
        rules = PermissionRuleset.all()
        assert rules.allow_bash is True
        assert rules.allow_network is True
        assert rules.allow_file_write is True

    def test_read_only(self):
        rules = PermissionRuleset.read_only()
        assert rules.can_use("read") is True
        assert rules.can_use("glob") is True
        assert rules.can_use("grep") is True
        assert rules.can_use("bash") is False
        assert rules.can_use("write") is False

    def test_model_only(self):
        rules = PermissionRuleset.model_only()
        assert rules.can_use("read") is False
        assert rules.can_use("bash") is False

    def test_default_permissions(self):
        rules = PermissionRuleset.default()
        assert rules.can_use("read") is True
        assert rules.can_use("task") is False
        assert rules.can_use("skill") is False


class TestBuiltinAgents:
    def test_has_eleven_agents(self):
        assert len(BUILTIN_AGENTS) == 11
        assert "build" in BUILTIN_AGENTS
        assert "plan" in BUILTIN_AGENTS
        assert "general" in BUILTIN_AGENTS
        assert "explore" in BUILTIN_AGENTS
        assert "code" in BUILTIN_AGENTS
        assert "test" in BUILTIN_AGENTS
        assert "document" in BUILTIN_AGENTS
        assert "review" in BUILTIN_AGENTS

    def test_build_agent_has_all_permissions(self):
        build = BUILTIN_AGENTS["build"]
        assert build.mode == "primary"
        assert build.permission.allow_bash is True

    def test_hidden_agents_not_in_list(self):
        registry = AgentRegistry(None)
        agents = registry.list(include_hidden=False)
        for a in agents:
            assert a.hidden is False

    def test_hidden_agents_in_list(self):
        registry = AgentRegistry(None)
        agents = registry.list(include_hidden=True)
        names = {a.name for a in agents}
        assert "compaction" in names
        assert "title" in names
        assert "summary" in names
