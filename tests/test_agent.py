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

    def test_subagent_permissions(self):
        rules = PermissionRuleset.subagent()
        assert rules.can_use("read") is True
        assert rules.can_use("write") is True
        assert rules.can_use("bash") is True
        assert rules.can_use("task") is False
        assert rules.can_use("question") is False


class TestBuiltinAgents:
    def test_has_ten_agents(self):
        assert len(BUILTIN_AGENTS) == 10
        assert "build" in BUILTIN_AGENTS
        assert "plan" in BUILTIN_AGENTS
        assert "general" in BUILTIN_AGENTS
        assert "explore" in BUILTIN_AGENTS
        assert "code" in BUILTIN_AGENTS
        assert "test" in BUILTIN_AGENTS
        assert "document" in BUILTIN_AGENTS
        assert "compaction" in BUILTIN_AGENTS
        assert "title" in BUILTIN_AGENTS
        assert "summary" in BUILTIN_AGENTS

    def test_build_agent_has_all_permissions(self):
        build = BUILTIN_AGENTS["build"]
        assert build.mode == "primary"
        assert build.permission.allow_bash is True

    def test_new_worker_agents_are_subagents(self):
        """验证新增的 worker 类型都是 subagent 模式。"""
        for agent_type in ["code", "test", "document"]:
            agent = BUILTIN_AGENTS[agent_type]
            assert agent.mode == "subagent", f"{agent_type} should be subagent"

    def test_document_agent_permissions(self):
        """验证 document agent 没有 bash 权限。"""
        doc = BUILTIN_AGENTS["document"]
        assert doc.permission.allow_bash is False
        assert doc.permission.allow_file_write is True

    def test_test_agent_permissions(self):
        """验证 test agent 有 bash 权限。"""
        test = BUILTIN_AGENTS["test"]
        assert test.permission.allow_bash is True
        assert test.permission.allow_file_write is True

    def test_code_agent_permissions(self):
        """验证 code agent 有完整权限。"""
        code = BUILTIN_AGENTS["code"]
        assert code.permission.allow_bash is True
        assert code.permission.allow_file_write is True
        assert "task" in code.permission.deny_tools

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
