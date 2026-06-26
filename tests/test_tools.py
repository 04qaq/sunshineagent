"""Tests for the tool system."""

import json

from src.tool.base import Tool, ToolContext, ToolRegistry, ToolResult


class MockTool(Tool):
    name = "mock"
    description = "A mock tool"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(output="mock output")


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)
        assert registry.get("mock") is tool

    def test_unregister(self):
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)
        registry.unregister("mock")
        assert registry.get("mock") is None

    def test_list_all(self):
        registry = ToolRegistry()
        registry.register(MockTool())
        assert len(registry.list_all()) == 1


class TestEditTool:
    async def test_edit_success(self, tmp_path):
        from src.tool.edit import EditTool

        file_path = tmp_path / "test.py"
        file_path.write_text("hello world")

        tool = EditTool(str(tmp_path))
        result = await tool.execute(
            {
                "filePath": str(file_path),
                "oldString": "hello world",
                "newString": "goodbye world",
            },
            ToolContext(
                session_id="s1",
                agent="build",
                assistant_message_id=None,
                tool_call_id="tc1",
            ),
        )
        assert "Successfully edited" in result.output
        assert file_path.read_text() == "goodbye world"


class TestBashTool:
    async def test_bash_echo(self):
        from src.tool.bash import BashTool

        tool = BashTool(str(__import__("pathlib").Path.cwd()))
        result = await tool.execute(
            {"command": "echo hello", "description": "test echo"},
            ToolContext(
                session_id="s1",
                agent="build",
                assistant_message_id=None,
                tool_call_id="tc1",
            ),
        )
        assert "hello" in result.output


class TestMessageConversion:
    def test_openai_message_order(self):
        """确保 assistant 消息在 tool_result 之前。"""
        from src.agent.loop import AgentLoop

        class MockMsg:
            def __init__(self, role, parts_str):
                self.role = role
                self.parts = parts_str

        messages = [
            MockMsg("user", json.dumps([{"type": "text", "text": "hello"}])),
            MockMsg(
                "assistant",
                json.dumps([
                    {"type": "text", "text": "let me read"},
                    {"type": "tool_call", "tool_call_id": "t1",
                     "tool_name": "read", "args": {"filePath": "/x"}},
                    {"type": "tool_result", "tool_call_id": "t1",
                     "output": "contents", "is_error": False},
                ]),
            ),
        ]

        result = AgentLoop._to_openai_messages(messages)
        roles = [m["role"] for m in result]
        # 顺序应为: user, assistant, tool
        assert roles == ["user", "assistant", "tool"], f"unexpected order: {roles}"
        # tool 消息必须有 tool_call_id
        assert result[2]["tool_call_id"] == "t1"
        # assistant 消息必须有 tool_calls
        assert "tool_calls" in result[1]
        assert result[1]["tool_calls"][0]["id"] == "t1"
    async def test_grep_match(self, tmp_path):
        from src.tool.grep import GrepTool

        f = tmp_path / "code.py"
        f.write_text("def foo():\n    return 42\n")

        tool = GrepTool(str(tmp_path))
        result = await tool.execute(
            {"pattern": "def foo", "path": str(tmp_path)},
            ToolContext(
                session_id="s1",
                agent="build",
                assistant_message_id=None,
                tool_call_id="tc1",
            ),
        )
        assert "def foo" in result.output
