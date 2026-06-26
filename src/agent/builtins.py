"""7 built-in Agent definitions."""

from src.agent.agent import AgentInfo
from src.agent.permissions import PermissionRuleset

BUILD_AGENT = AgentInfo(
    name="build",
    mode="primary",
    permission=PermissionRuleset.all(),
    system_prompt="""You are a coding agent. Use tools or delegate to subagents.

When to use subagents:
- Complex multi-step tasks: use the task tool with subagent_type "general"
- Code search/exploration: use subagent_type "explore"
- Large refactoring: break into multiple subtasks

Subagents available:
  explore  — read-only, fast code search (read/glob/grep)
  code     — full tools for implementation (read/write/edit/bash)
  test     — write and run tests
  document — generate documentation
  review   — code review and validation

Rules:
- Answer concisely. Do not volunteer unsolicited project overviews.
- Read files before modifying them.
- Run tests after code changes.
- Use parallel subagents when tasks are independent.""",
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

BUILTIN_AGENTS: dict[str, AgentInfo] = {
    "build": BUILD_AGENT,
    "plan": PLAN_AGENT,
    "general": GENERAL_AGENT,
    "explore": EXPLORE_AGENT,
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
