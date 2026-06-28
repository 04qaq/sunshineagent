"""Executive Controller - 执行复杂多步骤任务的控制器。

借鉴 Claude Code Coordinator-Worker 模式：
1. 生成 TaskGraph 计划
2. 按拓扑层级并行执行子任务
3. 合成义务：理解 Worker 结果，生成具体后续指令
4. 失败时 Reflection 分析并重试
5. 实时流式输出进度
6. 生成最终报告

关键设计约束（借鉴 Claude Code）：
- Coordinator 不能懒委托，必须理解 Worker 结果
- Worker 看不到用户对话，只看到 Coordinator 的 spec
- Continue vs Spawn 决策基于上下文质量
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.task_graph.graph import TaskGraph, TaskNode, TaskResult, TaskStatus


@dataclass
class ReflectionResult:
    """Reflection 分析结果。"""
    root_cause: str
    is_prompt_issue: bool = False
    is_context_issue: bool = False
    is_capability_issue: bool = False
    suggestions: list[str] = None
    revised_prompt: str = ""
    additional_files: list[str] = None

    def __post_init__(self):
        if self.suggestions is None:
            self.suggestions = []
        if self.additional_files is None:
            self.additional_files = []

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ReflectionResult:
        """从 JSON 创建。"""
        return cls(
            root_cause=data.get("root_cause", ""),
            is_prompt_issue=data.get("is_prompt_issue", False),
            is_context_issue=data.get("is_context_issue", False),
            is_capability_issue=data.get("is_capability_issue", False),
            suggestions=data.get("suggestions", []),
            revised_prompt=data.get("revised_prompt", ""),
            additional_files=data.get("additional_files", []),
        )


# Prompt 模板
PLAN_PROMPT_TEMPLATE = """你是一个任务规划专家。将用户需求分解为可执行的子任务图。

规则：
1. 每个子任务必须是独立可执行的
2. 明确任务间的依赖关系
3. 选择合适的 worker 类型
4. 任务数量控制在 3-10 个

Worker 类型：
- search: 搜索、扫描、分析代码（只读）
- code: 编写代码、实现功能、重构
- test: 编写测试、运行测试
- document: 编写文档、注释
- general: 通用任务

输出 JSON：
{{
  "nodes": [
    {{
      "id": "task_1",
      "task_type": "search|code|test|document|general",
      "description": "简短描述",
      "prompt": "详细执行提示",
      "dependencies": [],
      "relevant_files": ["path/to/file"],
      "quality": "low|medium|high"
    }}
  ]
}}

用户需求：{request}"""

REFLECTION_PROMPT_TEMPLATE = """分析子任务失败原因并给出修正建议。

任务信息：
- 任务描述：{task}
- 原始提示：{prompt}
- 执行次数：第 {attempt} 次
- 错误信息：{error}
- 错误历史：{error_history}

输出 JSON：
{{
  "root_cause": "根本原因",
  "is_prompt_issue": true/false,
  "is_context_issue": true/false,
  "is_capability_issue": true/false,
  "suggestions": ["建议1", "建议2"],
  "revised_prompt": "修正后的 prompt",
  "additional_files": ["需要添加的文件路径"]
}}"""

SYNTHESIS_PROMPT_TEMPLATE = """你是 Coordinator，需要合成 Worker 的结果。

禁止懒委托 - 你必须：
1. 理解 Worker 的发现和结果
2. 提取关键信息
3. 生成具体的后续指令（不能写"based on findings, fix it"）

Worker 结果：
{worker_results}

当前任务图状态：
{graph_state}

请输出 JSON：
{{
  "understanding": "你对 Worker 结果的理解",
  "key_findings": ["发现1", "发现2"],
  "next_actions": [
    {{
      "task_id": "要修改的任务ID",
      "action": "continue|spawn|skip",
      "revised_prompt": "如果是 continue，给出具体的修正指令",
      "reason": "决策原因"
    }}
  ],
  "should_update_plan": true/false,
  "plan_updates": ["如果需要更新计划，说明更新内容"]
}}"""

# Continue vs Spawn 决策矩阵
CONTINUE_VS_SPAWN_GUIDE = """
决策规则：
- 研究涉及的文件恰好需要修改 → Continue（Worker 已有文件上下文）
- 研究范围广但实现只改几个文件 → Spawn fresh（避免探索噪音）
- 纠正 Worker 的错误 → Continue（Worker 知道它试了什么）
- 验证另一个 Worker 的代码 → Spawn fresh（验证者需独立视角）
- 第一次尝试用了完全错误的方案 → Spawn fresh（错误上下文会污染）
- 完全无关的任务 → Spawn fresh（无上下文可复用）
"""


class ExecutiveController:
    """Executive 模式控制器（Coordinator-Worker 模式）。

    借鉴 Claude Code 的 Coordinator 设计：
    1. 调用 LLM 生成任务计划
    2. 调度执行子任务（通过 TaskTool）
    3. 合成义务：理解 Worker 结果，生成具体后续指令
    4. 失败时 Reflection + 重试
    5. 实时输出进度
    6. 生成最终报告

    关键约束：
    - 不能懒委托：必须理解 Worker 结果后生成具体 spec
    - Worker 看不到用户对话：prompt 必须自包含
    - Continue vs Spawn 决策：基于上下文质量
    """

    def __init__(
        self,
        task_executor: Callable,  # TaskTool.execute 的引用
        llm_caller: Callable,     # LLM 调用函数
        progress_callback: Callable[[str], None] | None = None,
    ):
        self._task_executor = task_executor
        self._llm_caller = llm_caller
        self._on_progress = progress_callback or (lambda x: None)
        self._worker_results: dict[str, TaskResult] = {}  # 存储 Worker 结果

    async def execute(
        self,
        request: str,
        session_id: str,
        max_parallel: int = 4,
    ) -> str:
        """执行主入口。

        Args:
            request: 用户需求
            session_id: 父会话 ID
            max_parallel: 最大并行数

        Returns:
            执行报告
        """
        start_time = time.time()

        # 1. 生成计划
        self._on_progress("\n正在分析任务，生成执行计划...\n")
        graph = await self._generate_plan(request)

        if not graph or not graph.nodes:
            return "无法生成任务计划，请尝试更详细地描述需求。"

        self._emit_plan(graph)
        self._on_progress("\n开始执行...\n\n")

        # 2. 执行循环
        max_iterations = 100  # 防止无限循环
        iteration = 0

        while not graph.all_completed() and iteration < max_iterations:
            iteration += 1

            ready_nodes = graph.get_ready_nodes()

            if not ready_nodes:
                if graph.is_blocked():
                    # 等待运行中的任务完成
                    await asyncio.sleep(0.1)
                    continue
                elif graph.has_failures():
                    # 有失败且无法重试的任务
                    break
                else:
                    # 没有可执行也没有运行中的任务，可能是死锁
                    self._on_progress("警告：任务图可能死锁\n")
                    break

            # 限制并行数
            batch = ready_nodes[:max_parallel]

            # 标记为运行中
            for node in batch:
                node.mark_running()
                self._on_progress(
                    f"[{node.id}] 开始执行: {node.description}\n"
                )

            # 并行执行
            tasks = [
                self._execute_node(node, session_id)
                for node in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理结果
            for node, result in zip(batch, results, strict=False):
                await self._handle_result(node, result, graph)

        # 3. 生成报告
        elapsed = time.time() - start_time
        report = self._generate_report(graph, elapsed)
        self._on_progress(f"\n{report}\n")

        return report

    async def _generate_plan(self, request: str) -> TaskGraph:
        """调用 LLM 生成任务计划。"""
        prompt = PLAN_PROMPT_TEMPLATE.format(request=request)

        try:
            response = await self._llm_caller(prompt)

            # 提取 JSON
            json_str = self._extract_json(response)
            data = json.loads(json_str)

            return TaskGraph.from_json(data)

        except Exception as e:
            self._on_progress(f"计划生成失败: {e}\n")
            return TaskGraph()

    async def _execute_node(self, node: TaskNode, session_id: str) -> TaskResult:
        """执行单个节点。"""
        try:
            result = await self._task_executor(
                params={
                    "description": node.description,
                    "prompt": node.prompt,
                    "subagent_type": node.task_type,
                    "relevant_files": node.relevant_files,
                    "run_in_background": False,
                },
                session_id=session_id,
            )

            # 解析结果
            if hasattr(result, "output"):
                success = not getattr(result, "is_error", False)
                return TaskResult(
                    success=success,
                    output=result.output if success else "",
                    error=result.output if not success else "",
                )
            else:
                return TaskResult(success=True, output=str(result))

        except Exception as e:
            return TaskResult(success=False, error=str(e))

    async def _handle_result(
        self,
        node: TaskNode,
        result: TaskResult | Exception,
        graph: TaskGraph,
    ):
        """处理执行结果。

        借鉴 Claude Code 的 Coordinator 合成义务：
        1. 存储 Worker 结果
        2. 理解结果内容
        3. 决定 Continue vs Spawn
        """
        if isinstance(result, Exception):
            # 执行异常
            error_msg = str(result)
            node.mark_failed(error_msg)

            if node.can_retry():
                # Reflection 分析
                analysis = await self._reflect_on_failure(node, error_msg)

                if analysis.revised_prompt:
                    node.prompt = analysis.revised_prompt
                if analysis.additional_files:
                    node.relevant_files.extend(analysis.additional_files)
                    node.relevant_files = list(set(node.relevant_files))

                self._on_progress(
                    f"[{node.id}] ✗ 失败，正在重试 ({node.retry_count}/{node.max_retries})\n"
                    f"  原因: {analysis.root_cause}\n"
                    f"  建议: {', '.join(analysis.suggestions)}\n\n"
                )
                node.reset_for_retry()
            else:
                self._on_progress(
                    f"[{node.id}] ✗ 最终失败: {error_msg}\n\n"
                )

        elif result.success:
            node.mark_completed(result)
            # 存储 Worker 结果用于合成
            self._worker_results[node.id] = result
            self._on_progress(f"[{node.id}] ✓ 完成\n\n")

        else:
            # 任务返回失败
            error_msg = result.error or "未知错误"
            node.mark_failed(error_msg)

            if node.can_retry():
                analysis = await self._reflect_on_failure(node, error_msg)

                if analysis.revised_prompt:
                    node.prompt = analysis.revised_prompt
                if analysis.additional_files:
                    node.relevant_files.extend(analysis.additional_files)
                    node.relevant_files = list(set(node.relevant_files))

                self._on_progress(
                    f"[{node.id}] ✗ 失败，正在重试 ({node.retry_count}/{node.max_retries})\n"
                    f"  原因: {analysis.root_cause}\n\n"
                )
                node.reset_for_retry()
            else:
                self._on_progress(
                    f"[{node.id}] ✗ 最终失败: {error_msg}\n\n"
                )

    async def synthesize_results(self, graph: TaskGraph) -> dict[str, Any]:
        """合成 Worker 结果（Coordinator 的核心职责）。

        借鉴 Claude Code 的合成义务：
        - 不能懒委托：必须理解 Worker 结果
        - 提取关键信息
        - 生成具体的后续指令

        Returns:
            合成结果，包含 understanding, key_findings, next_actions
        """
        if not self._worker_results:
            return {"understanding": "无 Worker 结果", "key_findings": [], "next_actions": []}

        # 构建 Worker 结果摘要
        results_summary = []
        for node_id, result in self._worker_results.items():
            node = graph.get_node(node_id)
            if node:
                results_summary.append(
                    f"[{node_id}] {node.description}:\n"
                    f"  状态: {node.status.value}\n"
                    f"  重试: {node.retry_count} 次\n"
                    f"  输出: {result.output[:500]}..."
                )

        # 构建任务图状态
        graph_state = graph.summary()

        # 调用 LLM 合成
        prompt = SYNTHESIS_PROMPT_TEMPLATE.format(
            worker_results="\n\n".join(results_summary),
            graph_state=graph_state,
        )

        try:
            response = await self._llm_caller(prompt)
            json_str = self._extract_json(response)
            return json.loads(json_str)
        except Exception as e:
            self._on_progress(f"合成失败: {e}\n")
            return {
                "understanding": "无法合成",
                "key_findings": [],
                "next_actions": [],
            }

    def _should_continue(self, node: TaskNode, result: TaskResult) -> bool:
        """决定是否 Continue（而非 Spawn fresh）。

        借鉴 Claude Code 的决策矩阵：
        - 研究涉及的文件恰好需要修改 → Continue
        - 纠正 Worker 的错误 → Continue
        - 验证另一个 Worker 的代码 → Spawn fresh
        - 完全无关的任务 → Spawn fresh
        """
        # 如果 Worker 成功完成，通常 Continue
        if result.success:
            return True

        # 如果是研究/搜索类型，可能需要 Spawn fresh
        return node.task_type not in ("search", "explore")

    async def _reflect_on_failure(self, node: TaskNode, error: str) -> ReflectionResult:
        """Reflection: 分析失败原因。"""
        prompt = REFLECTION_PROMPT_TEMPLATE.format(
            task=node.description,
            prompt=node.prompt,
            attempt=node.retry_count,
            error=error[:500],  # 限制长度
            error_history="\n".join(node.error_history[-3:]),  # 最近3次
        )

        try:
            response = await self._llm_caller(prompt)
            json_str = self._extract_json(response)
            data = json.loads(json_str)
            return ReflectionResult.from_json(data)
        except Exception:
            # Reflection 失败，返回默认值
            return ReflectionResult(
                root_cause="无法分析",
                suggestions=["检查任务描述", "添加更多上下文"],
            )

    def _emit_plan(self, graph: TaskGraph):
        """输出计划。"""
        levels = graph.topological_levels()
        self._on_progress("\n执行计划：\n")

        task_num = 0
        for _, level in enumerate(levels):
            for node_id in level:
                task_num += 1
                node = graph.nodes[node_id]
                deps = ", ".join(node.dependencies) if node.dependencies else "无"
                self._on_progress(
                    f"  [{task_num}/{len(graph.nodes)}] {node.id}: {node.description} "
                    f"({node.task_type}) <- 依赖: {deps}\n"
                )

        self._on_progress(f"\n共 {len(graph.nodes)} 个任务\n")

    def _generate_report(self, graph: TaskGraph, elapsed: float) -> str:
        """生成最终报告。"""
        completed = graph.get_completed_nodes()
        failed = graph.get_failed_nodes()
        retried = [n for n in completed if n.retry_count > 0]

        lines = ["执行完成：\n"]

        for node in graph.nodes.values():
            if node.status == TaskStatus.COMPLETED:
                status = "✓"
                retry_info = f" (重试 {node.retry_count} 次)" if node.retry_count > 0 else ""
            else:
                status = "✗"
                retry_info = ""
            lines.append(f"  [{node.id}] {status} {node.description}{retry_info}\n")

        lines.append(
            f"\n总计: {len(graph.nodes)} 个任务, "
            f"{len(completed)} 个成功, "
            f"{len(failed)} 个失败"
        )

        if retried:
            lines.append(f", {len(retried)} 个重试后成功")

        lines.append(f"\n耗时: {elapsed:.1f} 秒")

        return "".join(lines)

    def _extract_json(self, text: str) -> str:
        """从文本中提取 JSON 字符串。"""
        # 尝试直接解析
        text = text.strip()
        if text.startswith("{"):
            return text

        # 尝试找到 JSON 块
        import re
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            return json_match.group(1).strip()

        # 尝试找到第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]

        return text
