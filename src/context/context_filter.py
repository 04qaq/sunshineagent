"""Context Filter - 上下文过滤器。

根据 agent 类型和任务需求过滤上下文内容，实现三层隔离：
- Session 层：完整历史
- Task 层：任务相关上下文
- Worker 层：精简上下文
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FilterConfig:
    """过滤器配置。"""
    max_tokens: int = 4000
    include_file_contents: bool = True
    include_tool_results: bool = False
    include_system_messages: bool = False
    relevant_file_patterns: list[str] = field(default_factory=list)


# 不同 Worker 类型的默认配置
WORKER_FILTER_CONFIGS: dict[str, FilterConfig] = {
    "general": FilterConfig(
        max_tokens=6000,
        include_file_contents=True,
        include_tool_results=True,
        include_system_messages=False,
    ),
    "explore": FilterConfig(
        max_tokens=4000,
        include_file_contents=True,
        include_tool_results=False,
        include_system_messages=False,
    ),
    "code": FilterConfig(
        max_tokens=5000,
        include_file_contents=True,
        include_tool_results=True,
        include_system_messages=False,
    ),
    "test": FilterConfig(
        max_tokens=4000,
        include_file_contents=True,
        include_tool_results=True,
        include_system_messages=False,
    ),
    "document": FilterConfig(
        max_tokens=3000,
        include_file_contents=True,
        include_tool_results=False,
        include_system_messages=False,
    ),
}


class ContextFilter:
    """上下文过滤器 - 根据 agent 类型和任务需求过滤内容。"""

    def __init__(self):
        self._configs = WORKER_FILTER_CONFIGS.copy()

    def get_config(self, agent_type: str) -> FilterConfig:
        """获取指定 agent 类型的过滤配置。"""
        return self._configs.get(agent_type, FilterConfig())

    def set_config(self, agent_type: str, config: FilterConfig):
        """设置指定 agent 类型的过滤配置。"""
        self._configs[agent_type] = config

    def filter_for_worker(
        self,
        messages: list[Any],
        agent_type: str,
        task_description: str,
        relevant_files: list[str] | None = None,
    ) -> str:
        """为 Worker 过滤上下文。

        Args:
            messages: 父 session 的消息列表
            agent_type: Worker 类型
            task_description: 任务描述
            relevant_files: 相关文件列表

        Returns:
            过滤后的上下文字符串
        """
        config = self.get_config(agent_type)
        filtered_parts: list[str] = []

        # 1. 添加任务描述
        filtered_parts.append(f"Task: {task_description}")

        # 2. 提取相关文件内容
        if config.include_file_contents and relevant_files:
            file_contents = self._extract_file_contents(messages, relevant_files)
            if file_contents:
                filtered_parts.append("")
                filtered_parts.append("Relevant File Contents:")
                filtered_parts.extend(file_contents)

        # 3. 提取相关工具结果
        if config.include_tool_results:
            tool_results = self._extract_tool_results(messages, task_description)
            if tool_results:
                filtered_parts.append("")
                filtered_parts.append("Relevant Tool Results:")
                filtered_parts.extend(tool_results)

        # 4. 提取相关文本内容
        relevant_texts = self._extract_relevant_texts(messages, task_description)
        if relevant_texts:
            filtered_parts.append("")
            filtered_parts.append("Relevant Context:")
            filtered_parts.extend(relevant_texts)

        # 5. 截断到最大 token 数
        result = "\n".join(filtered_parts)
        if config.max_tokens > 0:
            result = self._truncate_to_tokens(result, config.max_tokens)

        return result

    def extract_relevant_files(
        self,
        messages: list[Any],
        task_description: str,
    ) -> list[str]:
        """从历史消息中提取相关文件路径。

        Args:
            messages: 消息列表
            task_description: 任务描述

        Returns:
            相关文件路径列表
        """
        file_paths: set[str] = set()
        task_keywords = set(task_description.lower().split())

        for msg in messages:
            if not msg.parts:
                continue

            try:
                parts = json.loads(msg.parts)
            except (json.JSONDecodeError, TypeError):
                continue

            for part in parts:
                # 从工具调用中提取文件路径
                if part.get("type") == "tool_call":
                    args = part.get("args", {})
                    if isinstance(args, dict):
                        for key in ["path", "file_path", "filename"]:
                            if key in args:
                                file_path = args[key]
                                if self._is_relevant_file(file_path, task_keywords):
                                    file_paths.add(file_path)

                # 从文本中提取文件路径
                elif part.get("type") == "text":
                    text = part.get("text", "")
                    paths = self._extract_paths_from_text(text)
                    for path in paths:
                        if self._is_relevant_file(path, task_keywords):
                            file_paths.add(path)

        return list(file_paths)

    def _extract_file_contents(
        self,
        messages: list[Any],
        relevant_files: list[str],
    ) -> list[str]:
        """从消息中提取相关文件的内容。"""
        contents: list[str] = []
        file_contents: dict[str, str] = {}

        # 收集所有文件内容
        for msg in messages:
            if not msg.parts:
                continue

            try:
                parts = json.loads(msg.parts)
            except (json.JSONDecodeError, TypeError):
                continue

            for part in parts:
                # 从工具结果中提取文件内容
                if part.get("type") == "tool_result":
                    output = part.get("output", "")
                    # 检查是否是文件读取结果
                    for file_path in relevant_files:
                        if file_path in output:
                            file_contents[file_path] = output[:2000]  # 限制长度

        # 组织输出
        for file_path in relevant_files:
            if file_path in file_contents:
                contents.append(f"--- {file_path} ---")
                contents.append(file_contents[file_path])
                contents.append("")

        return contents

    def _extract_tool_results(
        self,
        messages: list[Any],
        task_description: str,
    ) -> list[str]:
        """从消息中提取相关的工具结果。"""
        results: list[str] = []
        task_keywords = set(task_description.lower().split())

        for msg in messages:
            if not msg.parts:
                continue

            try:
                parts = json.loads(msg.parts)
            except (json.JSONDecodeError, TypeError):
                continue

            for part in parts:
                if part.get("type") == "tool_result":
                    output = part.get("output", "")
                    tool_name = part.get("tool_name", "")

                    # 检查是否与任务相关
                    if self._is_relevant_content(output, task_keywords):
                        # 截断长输出
                        truncated = output[:500] if len(output) > 500 else output
                        results.append(f"[{tool_name}]: {truncated}")

        return results[:5]  # 最多5个结果

    def _extract_relevant_texts(
        self,
        messages: list[Any],
        task_description: str,
    ) -> list[str]:
        """从消息中提取相关的文本内容。"""
        texts: list[str] = []
        task_keywords = set(task_description.lower().split())

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
                    if self._is_relevant_content(text, task_keywords):
                        # 截断长文本
                        truncated = text[:300] if len(text) > 300 else text
                        texts.append(truncated)

        return texts[:3]  # 最多3个片段

    def _is_relevant_content(self, content: str, keywords: set[str]) -> bool:
        """判断内容是否与任务相关。"""
        content_lower = content.lower()
        return any(len(keyword) > 3 and keyword in content_lower for keyword in keywords)

    def _is_relevant_file(self, file_path: str, keywords: set[str]) -> bool:
        """判断文件是否与任务相关。"""
        path_lower = file_path.lower()
        return any(len(keyword) > 3 and keyword in path_lower for keyword in keywords)

    def _extract_paths_from_text(self, text: str) -> list[str]:
        """从文本中提取文件路径。"""
        # 匹配常见的文件路径模式
        patterns = [
            r'[\w/\\]+\.\w+',  # 简单路径
            r'(?:src|test|tests|lib|app)/[\w/\\]+\.\w+',  # 项目路径
        ]

        paths: list[str] = []
        for pattern in patterns:
            matches = re.findall(pattern, text)
            paths.extend(matches)

        return paths

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """截断文本到指定 token 数（近似）。"""
        # 简单估算：1 token ≈ 4 字符
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text

        # 截断并添加省略号
        return text[:max_chars - 20] + "\n... [truncated]"
