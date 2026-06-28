"""10 built-in Agent definitions."""

from src.agent.agent import AgentInfo
from src.agent.permissions import PermissionRuleset

BUILD_AGENT = AgentInfo(
    name="build",
    mode="primary",
    permission=PermissionRuleset.all(),
    system_prompt="""You are a coding agent. Use tools or delegate to subagents.

EXECUTIVE MODE (for complex multi-step tasks):
When receiving complex tasks that require multiple steps
(e.g., refactoring, implementing features with tests, migrating code),
use Executive mode:
- Call task tool with executive=true
- The system will automatically:
  1) Generate a task dependency graph
  2) Execute subtasks in parallel where possible
  3) Retry failed tasks with reflection
  4) Provide a final summary

Example: "Refactor the auth module and add tests"
-> Use executive=true

NORMAL MODE (for simple tasks):
For simple, single-step tasks, use normal subagent delegation:
- Call task tool with executive=false (default)

Subagents available:
  general - full tools for complex tasks
  explore - read-only, fast code search
  code - focused on writing clean, testable code
  test - focused on writing and running tests
  document - read-only + write docs

Rules:
- Answer concisely. Do not volunteer unsolicited project overviews.
- Read files before modifying them.
- Run tests after code changes.
- Use parallel subagents when tasks are independent.
- Use Executive mode for complex multi-step tasks.""",
)

PLAN_AGENT = AgentInfo(
    name="plan",
    mode="primary",
    permission=PermissionRuleset(readonly=True, write_plan=True),
)

GENERAL_AGENT = AgentInfo(
    name="general",
    mode="subagent",
    permission=PermissionRuleset.default(),
)

EXPLORE_AGENT = AgentInfo(
    name="explore",
    mode="subagent",
    permission=PermissionRuleset.read_only(),
)

COMPACTION_AGENT = AgentInfo(
    name="compaction",
    mode="primary",
    hidden=True,
    permission=PermissionRuleset.model_only(),
    system_prompt=(
        "You are a conversation summarizer. Summarize the provided conversation "
        "history into a structured summary including: Goal, Constraints, Progress, "
        "Key Decisions, Next Steps, Critical Context, and Relevant Files."
    ),
)

TITLE_AGENT = AgentInfo(
    name="title",
    mode="primary",
    hidden=True,
    permission=PermissionRuleset.model_only(),
)

SUMMARY_AGENT = AgentInfo(
    name="summary",
    mode="primary",
    hidden=True,
    permission=PermissionRuleset.model_only(),
)

CODE_AGENT = AgentInfo(
    name="code",
    mode="subagent",
    permission=PermissionRuleset(
        allow_bash=True,
        allow_network=True,
        allow_file_write=True,
        deny_tools={"task", "question"},
    ),
    system_prompt=(
        "You are a coding worker focused on writing clean, testable code. "
        "Follow best practices: read files before modifying, write idiomatic code, "
        "and ensure changes are backward compatible when possible."
    ),
)

TEST_AGENT = AgentInfo(
    name="test",
    mode="subagent",
    permission=PermissionRuleset(
        allow_bash=True,
        allow_network=True,
        allow_file_write=True,
        deny_tools={"task", "question"},
    ),
    system_prompt=(
        "You are a testing worker. Write comprehensive tests to verify code quality. "
        "Focus on: unit tests for new functionality, integration tests for critical paths, "
        "and edge case coverage. Run tests to ensure they pass."
    ),
)

DOCUMENT_AGENT = AgentInfo(
    name="document",
    mode="subagent",
    permission=PermissionRuleset(
        allow_bash=False,
        allow_network=False,
        allow_file_write=True,
        deny_tools={"task", "question", "bash"},
    ),
    system_prompt=(
        "You are a documentation worker. Generate clear, comprehensive documentation. "
        "Read existing code to understand functionality, then write accurate docs. "
        "Focus on: API documentation, usage examples, and architectural explanations."
    ),
)

BUILTIN_AGENTS: dict[str, AgentInfo] = {
    "build": BUILD_AGENT,
    "plan": PLAN_AGENT,
    "general": GENERAL_AGENT,
    "explore": EXPLORE_AGENT,
    "code": CODE_AGENT,
    "test": TEST_AGENT,
    "document": DOCUMENT_AGENT,
    "compaction": COMPACTION_AGENT,
    "title": TITLE_AGENT,
    "summary": SUMMARY_AGENT,
}


class AgentRegistry:
    """Agent registry supporting built-in + DB-persisted agents."""

    def __init__(self, db_session_factory):
        self._db = db_session_factory

    def get(self, name: str) -> AgentInfo | None:
        if name in BUILTIN_AGENTS:
            return BUILTIN_AGENTS[name]
        raise NotImplementedError("DB-based agent lookup not yet implemented")

    def list(self, *, include_hidden: bool = False) -> list[AgentInfo]:
        agents = list(BUILTIN_AGENTS.values())
        if not include_hidden:
            agents = [a for a in agents if not a.hidden]
        return agents
