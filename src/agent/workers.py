"""Worker Agent 类型 —— 按能力拆分的专业化子 Agent。

四种 Worker：
  - explore:  只读工具，快速搜索和分析代码
  - code:     完整工具集，编写和修改代码
  - test:     编写和运行测试
  - document: 生成文档
  - review:   审查和验收（强模型）
"""

from dataclasses import dataclass as _dc
from dataclasses import field as _f

from src.agent.agent import AgentInfo
from src.agent.permissions import PermissionRuleset

# ── Explore Worker ────────────────────────────────────────────────────

EXPLORE_WORKER = AgentInfo(
    name="explore",
    mode="subagent",
    permission=PermissionRuleset.read_only(),
    system_prompt="""You are a fast code search and analysis agent.

Your role:
- Search codebases thoroughly using glob, grep, and read tools
- Summarize findings concisely
- Identify patterns, dependencies, and key files
- Return structured results: files found, key patterns, relevant code snippets

Rules:
- Use parallel tool calls when searching multiple patterns
- Report file paths and line numbers
- Do NOT modify any files
- Limit output to what the caller needs""",
    temperature=0.3,
    max_steps=15,
)

# ── Code Worker ───────────────────────────────────────────────────────

CODE_WORKER = AgentInfo(
    name="code",
    mode="subagent",
    permission=PermissionRuleset.default(),
    system_prompt="""You are a code implementation agent.

Your role:
- Write, edit, and refactor code following the project's conventions
- Run tests to verify your changes
- Return the result: what was changed, what tests pass

Rules:
- Read relevant files FIRST before making changes
- Follow existing code style and patterns
- Keep changes minimal and focused
- Run relevant tests after changes
- Report what you changed and why""",
    temperature=0.3,
    max_steps=30,
)

# ── Test Worker ───────────────────────────────────────────────────────

TEST_WORKER = AgentInfo(
    name="test",
    mode="subagent",
    permission=PermissionRuleset.default(),
    system_prompt="""You are a test writing and verification agent.

Your role:
- Write unit tests for the specified code
- Run tests and report results
- Fix failing tests if the issue is in the test code

Rules:
- Follow the project's existing test framework and patterns
- Cover edge cases and error paths
- Run the tests to verify they pass
- Report: tests written, coverage, any failures""",
    temperature=0.2,
    max_steps=20,
)

# ── Document Worker ───────────────────────────────────────────────────

DOCUMENT_WORKER = AgentInfo(
    name="document",
    mode="subagent",
    permission=PermissionRuleset.read_only(),
    system_prompt="""You are a documentation generation agent.

Your role:
- Generate clear, concise documentation from code
- Write docstrings, READMEs, and API docs
- Do NOT modify any code

Rules:
- Read the source files first
- Generate documentation in the requested format
- Be concise but complete""",
    temperature=0.4,
    max_steps=10,
)

# ── Review Worker ─────────────────────────────────────────────────────

REVIEW_WORKER = AgentInfo(
    name="review",
    mode="subagent",
    permission=PermissionRuleset.read_only(),
    system_prompt="""You are a code review agent.

Your role:
- Review code changes for correctness, style, and safety
- Verify the changes match the task requirements
- Flag issues: bugs, style violations, missing edge cases

Rules:
- Read the changed files using the read tool
- Compare against the task specification
- Return: pass/fail + specific feedback
- Be critical but constructive""",
    temperature=0.2,
    max_steps=10,
)

# ── 注册表 ───────────────────────────────────────────────────────────

WORKERS: dict[str, AgentInfo] = {
    "explore": EXPLORE_WORKER,
    "code": CODE_WORKER,
    "test": TEST_WORKER,
    "document": DOCUMENT_WORKER,
    "review": REVIEW_WORKER,
}


def get_worker(task_type: str) -> AgentInfo:
    """按任务类型获取 Worker Agent。"""
    return WORKERS.get(task_type, CODE_WORKER)


# ── 任务需求定义（供 CapabilityRouter 使用）─────────────────────────


@_dc
class TaskRequirement:
    task_type: str = ""
    capabilities: list[str] = _f(default_factory=list)
    quality: str = "medium"
    budget: str = "medium"


TASK_REQUIREMENTS: dict[str, TaskRequirement] = {
    "plan": TaskRequirement(
        task_type="plan",
        capabilities=["planning", "architecture", "reasoning"],
        quality="high",
        budget="high",
    ),
    "explore": TaskRequirement(
        task_type="explore",
        capabilities=["search"],
        quality="low",
        budget="low",
    ),
    "code": TaskRequirement(
        task_type="code",
        capabilities=["code_generation", "reasoning"],
        quality="high",
        budget="medium",
    ),
    "test": TaskRequirement(
        task_type="test",
        capabilities=["code_generation", "test"],
        quality="medium",
        budget="low",
    ),
    "review": TaskRequirement(
        task_type="review",
        capabilities=["review", "code_generation", "reasoning"],
        quality="high",
        budget="high",
    ),
    "document": TaskRequirement(
        task_type="document",
        capabilities=["document"],
        quality="low",
        budget="low",
    ),
    "general": TaskRequirement(
        task_type="general",
        capabilities=["code_generation", "reasoning", "general"],
        quality="medium",
        budget="medium",
    ),
}
