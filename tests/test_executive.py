"""Tests for Executive Controller."""

import pytest

from src.agent.executive import ExecutiveController, ReflectionResult
from src.task_graph.graph import TaskGraph, TaskNode, TaskResult, TaskStatus


@pytest.fixture
def mock_task_executor():
    """模拟任务执行器。"""
    async def executor(params, session_id):
        return TaskResult(success=True, output=f"Completed: {params['description']}")
    return executor


@pytest.fixture
def mock_llm_caller():
    """模拟 LLM 调用。"""
    async def caller(prompt):
        # 返回一个简单的任务图
        return '''
        {
            "nodes": [
                {
                    "id": "task_1",
                    "task_type": "search",
                    "description": "Scan project",
                    "prompt": "Scan the project structure",
                    "dependencies": [],
                    "relevant_files": [],
                    "quality": "medium"
                },
                {
                    "id": "task_2",
                    "task_type": "code",
                    "description": "Implement feature",
                    "prompt": "Implement the feature",
                    "dependencies": ["task_1"],
                    "relevant_files": [],
                    "quality": "high"
                }
            ]
        }
        '''
    return caller


@pytest.fixture
def progress_messages():
    """收集进度消息。"""
    messages = []
    def callback(text):
        messages.append(text)
    return messages, callback


class TestReflectionResult:
    def test_from_json(self):
        """测试从 JSON 创建。"""
        data = {
            "root_cause": "Missing file",
            "is_prompt_issue": False,
            "is_context_issue": True,
            "suggestions": ["Add file path"],
            "revised_prompt": "",
            "additional_files": ["src/auth.py"],
        }
        result = ReflectionResult.from_json(data)
        assert result.root_cause == "Missing file"
        assert result.is_context_issue is True
        assert "src/auth.py" in result.additional_files

    def test_defaults(self):
        """测试默认值。"""
        result = ReflectionResult(root_cause="test")
        assert result.is_prompt_issue is False
        assert result.suggestions == []
        assert result.additional_files == []


class TestExecutiveController:
    @pytest.mark.asyncio
    async def test_generate_plan(self, mock_llm_caller):
        """测试生成计划。"""
        controller = ExecutiveController(
            task_executor=None,
            llm_caller=mock_llm_caller,
        )

        graph = await controller._generate_plan("Test request")

        assert len(graph.nodes) == 2
        assert "task_1" in graph.nodes
        assert "task_2" in graph.nodes
        assert graph.nodes["task_2"].dependencies == ["task_1"]

    @pytest.mark.asyncio
    async def test_execute_success(self, mock_task_executor, mock_llm_caller, progress_messages):
        """测试成功执行。"""
        messages, callback = progress_messages
        controller = ExecutiveController(
            task_executor=mock_task_executor,
            llm_caller=mock_llm_caller,
            progress_callback=callback,
        )

        report = await controller.execute("Test request", "session_1")

        assert "执行完成" in report
        assert "2 个任务" in report
        assert "2 个成功" in report
        assert "task_1" in report
        assert "task_2" in report

    @pytest.mark.asyncio
    async def test_execute_with_failure_and_retry(self, mock_llm_caller, progress_messages):
        """测试失败和重试。"""
        messages, callback = progress_messages
        call_count = 0

        async def failing_then_success(params, session_id):
            nonlocal call_count
            call_count += 1
            if params["description"] == "Scan project" and call_count <= 1:
                raise Exception("Temporary error")
            return TaskResult(success=True, output="Done")

        # 模拟 reflection 返回修正建议
        async def llm_with_reflection(prompt):
            if "分析子任务失败原因" in prompt:
                return '''
                {
                    "root_cause": "Temporary error",
                    "is_prompt_issue": False,
                    "is_context_issue": False,
                    "suggestions": ["Retry"],
                    "revised_prompt": ""
                }
                '''
            return await mock_llm_caller(prompt)

        controller = ExecutiveController(
            task_executor=failing_then_success,
            llm_caller=llm_with_reflection,
            progress_callback=callback,
        )

        report = await controller.execute("Test request", "session_1")

        assert "执行完成" in report
        assert "重试" in report

    def test_extract_json(self, mock_task_executor):
        """测试 JSON 提取。"""
        controller = ExecutiveController(
            task_executor=mock_task_executor,
            llm_caller=None,
        )

        # 直接 JSON
        assert controller._extract_json('{"key": "value"}') == '{"key": "value"}'

        # 带 markdown 块的 JSON
        text = '```json\n{"key": "value"}\n```'
        assert controller._extract_json(text) == '{"key": "value"}'

        # 带文本的 JSON
        text = 'Here is the result:\n{"key": "value"}\nDone.'
        assert controller._extract_json(text) == '{"key": "value"}'

    def test_generate_report(self, mock_task_executor):
        """测试生成报告。"""
        controller = ExecutiveController(
            task_executor=mock_task_executor,
            llm_caller=None,
        )

        graph = TaskGraph([
            TaskNode(id="t1", task_type="code", description="Task 1", prompt=""),
            TaskNode(id="t2", task_type="test", description="Task 2", prompt=""),
            TaskNode(id="t3", task_type="code", description="Task 3", prompt=""),
        ])

        graph.nodes["t1"].mark_completed(TaskResult(success=True))
        graph.nodes["t2"].mark_completed(TaskResult(success=True))
        graph.nodes["t2"].retry_count = 1  # 重试过一次
        graph.nodes["t3"].mark_failed("Error")

        report = controller._generate_report(graph, 10.5)

        assert "执行完成" in report
        assert "✓" in report
        assert "✗" in report
        assert "重试 1 次" in report
        assert "10.5 秒" in report
