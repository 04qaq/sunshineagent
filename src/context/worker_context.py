"""Worker Context Builder - 构建精简的 Worker 上下文。

对应架构文档中的 Context Manager Worker 层隔离：
  WorkerContext = TaskSpec.relevantFiles + TaskSpec.prompt + SystemPrompt.minimal
  NOT: 完整对话历史
  NOT: 不相关的文件内容
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskSpec:
    """任务规格定义。"""
    description: str
    prompt: str
    relevant_files: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    quality: str = "medium"  # low/medium/high
    budget: str = "medium"  # low/medium/high


@dataclass
class DependencyResult:
    """依赖任务的结果。"""
    task_id: str
    status: str  # completed/failed/cancelled
    summary: str = ""
    output: str = ""
    files_modified: list[str] = field(default_factory=list)


class WorkerContextBuilder:
    """Worker 上下文构建器 - 生成精简的任务上下文。

    职责：
    1. 从任务规格构建上下文
    2. 注入依赖任务的结果摘要
    3. 过滤不必要的历史信息
    """

    def build(
        self,
        task_spec: TaskSpec,
        agent_type: str,
        dependency_results: dict[str, DependencyResult] | None = None,
        parent_context: str | None = None,
    ) -> str:
        """构建 Worker 上下文。

        Args:
            task_spec: 任务规格
            agent_type: Worker 类型 (general/explore/code/test/document)
            dependency_results: 依赖任务的结果
            parent_context: 父上下文摘要（可选）

        Returns:
            精简的上下文字符串
        """
        parts: list[str] = []

        # 1. 任务描述
        parts.append(f"Task: {task_spec.description}")
        parts.append(f"Goal: {task_spec.prompt}")

        # 2. 相关文件
        if task_spec.relevant_files:
            parts.append("")
            parts.append("Relevant Files:")
            for f in task_spec.relevant_files:
                parts.append(f"  - {f}")

        # 3. 依赖结果摘要
        if dependency_results:
            parts.append("")
            parts.append("Previous Results:")
            for dep_id, result in dependency_results.items():
                status_icon = "✓" if result.status == "completed" else "✗"
                parts.append(f"  [{status_icon} {dep_id}]: {result.summary}")
                if result.files_modified:
                    parts.append(f"    Modified files: {', '.join(result.files_modified)}")

        # 4. 父上下文摘要（如果有）
        if parent_context:
            parts.append("")
            parts.append("Project Context:")
            parts.append(parent_context)

        # 5. Worker 类型特定指令
        parts.append("")
        parts.append(self._get_agent_instructions(agent_type))

        return "\n".join(parts)

    def _get_agent_instructions(self, agent_type: str) -> str:
        """获取 Worker 类型特定的指令。"""
        instructions = {
            "general": (
                "You are a general-purpose worker. "
                "Complete the task using all available tools."
            ),
            "explore": (
                "You are an exploration worker. "
                "Search and analyze code without making changes."
            ),
            "code": (
                "You are a coding worker. "
                "Write clean, testable code following project conventions."
            ),
            "test": (
                "You are a testing worker. "
                "Write and run tests to verify code quality."
            ),
            "document": (
                "You are a documentation worker. "
                "Generate clear, accurate documentation."
            ),
        }
        return instructions.get(agent_type, instructions["general"])

    def build_from_messages(
        self,
        messages: list[Any],
        task_description: str,
        agent_type: str,
        max_context_tokens: int = 4000,
    ) -> str:
        """从历史消息构建精简上下文。

        用于从父 session 消息中提取相关上下文。

        Args:
            messages: 父 session 的消息列表
            task_description: 任务描述
            agent_type: Worker 类型
            max_context_tokens: 最大上下文 token 数

        Returns:
            精简的上下文字符串
        """
        relevant_parts: list[str] = []

        # 从历史消息中提取相关信息
        for msg in messages:
            if not msg.parts:
                continue

            try:
                parts = json.loads(msg.parts)
            except (json.JSONDecodeError, TypeError):
                continue

            for part in parts:
                if part.get("type") == "text":
                    text = part.get("text", "")
                    # 只保留与任务相关的内容
                    if self._is_relevant(text, task_description):
                        relevant_parts.append(text[:500])  # 限制长度

        # 组合上下文
        context = f"Task: {task_description}\n"
        if relevant_parts:
            context += "\nRelevant Context:\n"
            for part in relevant_parts[:3]:  # 最多3个相关片段
                context += f"  {part}\n"

        return context

    def _is_relevant(self, text: str, task_description: str) -> bool:
        """判断文本是否与任务相关。"""
        # 简单的关键词匹配
        task_keywords = set(task_description.lower().split())
        text_lower = text.lower()

        # 检查是否有关键词重叠
        return any(len(keyword) > 3 and keyword in text_lower for keyword in task_keywords)
