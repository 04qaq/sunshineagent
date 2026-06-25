# SunshineAgent

SunshineAgent is a Python reimplementation of the [OpenCode](https://github.com/anomalyco/opencode) AI coding agent framework, using Python/asyncio idioms instead of TypeScript/Effect-TS.

Full architecture and implementation reference: `docs/python-agent-development-plan.md`
Deep architecture analysis: `docs/architecture-analysis.md`
Reference source: `docs/参考源码/opencode/`

---

## Ownership — Who Implements What

The project is divided into two parts to balance learning with productivity.
**The human implements core modules** that teach fundamental Python skills.
**The AI agent implements everything else.**

If the human's module is not yet done, the agent may create a stub/placeholder so other modules can still work.

### Human's Modules (do not implement these unless asked)

| Module | Files | Skills Practiced |
|--------|-------|-----------------|
| **Data Models** | `src/models/session.py`, `src/models/message.py`, `src/models/__init__.py` | SQLAlchemy 2.0 async, dataclass, JSON column |
| **Agent Registry** | `src/agent/agent.py`, `src/agent/__init__.py` | dataclass, registry pattern |
| **Tool System** | `src/tool/base.py`, `src/tool/read.py`, `src/tool/write.py`, `src/tool/glob.py`, `src/tool/__init__.py` | abstract class, async file I/O, pathlib |
| **Agent Loop (single turn)** | `src/agent/loop.py` — `_run_turn()` method only | async streaming, tool settlement, LLM response parsing |
| **Prompt Engine** | `src/prompt/engine.py`, `src/prompt/__init__.py` | Jinja2 templates, message format conversion |
| **CLI Entry** | `src/cli/main.py`, `src/cli/__init__.py` | Typer, Rich |

### Agent's Modules (implement these proactively)

Everything else not listed above, including:

- **Database setup**: `src/models/database.py` — async engine, session factory
- **SessionService**: `src/session/service.py` — CRUD, fork, remove
- **Agent Loop (multi-turn)**: `src/agent/loop.py` — `_run_loop()`, exit conditions, step limit
- **RunCoordinator**: `src/session/coordinator.py` — FIFO serialization, concurrent control
- **CompactionService**: `src/session/compaction.py` — context compaction
- **Provider layer**: `src/provider/*` — AnthropicClient, OpenAIClient, ModelCatalog, factory
- **Remaining tools**: `src/tool/edit.py`, `src/tool/bash.py`, `src/tool/grep.py`, `src/tool/task.py`, `src/tool/webfetch.py`, `src/tool/websearch.py`, `src/tool/question.py`, `src/tool/skill_tool.py`, `src/tool/todowrite.py`, `src/tool/apply_patch.py`, `src/tool/lsp.py`, `src/tool/plan_exit.py`
- **MCP integration**: `src/mcp/*`
- **Context engine**: `src/context/*`
- **Skill system**: `src/skill/*`
- **Background jobs**: `src/background/*`
- **Config**: `src/config/*` — Pydantic settings, YAML
- **All prompt templates**: `prompts/*.txt`
- **All tests**: `tests/*`
- **pyproject.toml**, **.gitignore**, **logging setup**
- **Phase 2-4** tasks from the development plan

---

## When the Agent Starts Writing Code

The agent should create the project skeleton first:
1. Write `pyproject.toml` with dependencies from the dev plan §15
2. Write `.gitignore`
3. Create the directory structure from the dev plan §14
4. Set up `src/models/database.py` (async engine)
5. Create stubs for the human's modules with correct function signatures (so the agent's modules can import them)
6. Then implement the agent's own modules

For the human's modules, provide stub files with:
- Correct function/class signatures
- `raise NotImplementedError` in method bodies
- Import statements the agent's modules will need

---

## Tech Stack

| Concern | Choice |
|---------|--------|
| Async runtime | asyncio + TaskGroup (Python 3.11+) |
| ORM | SQLAlchemy 2.0 async + aiosqlite |
| LLM client | OpenAI SDK + Anthropic SDK (direct) |
| CLI | Typer + Rich |
| Templates | Jinja2 |
| Settings | Pydantic + pydantic-settings + YAML |
| Logging | structlog |
| IDs | python-ulid |
| HTTP | httpx |
| MCP | mcp SDK |
| Testing | pytest + pytest-asyncio |
| Linting | ruff |

---

## Coding Conventions

### Python Style
- Follow PEP 8. Use `ruff` for linting.
- Type annotations on all function signatures (parameters and return).
- Use `str | None` not `Optional[str]`.
- Use `X | Y` union syntax (Python 3.10+), not `Union[X, Y]`.
- Use `list[X]` not `List[X]` from typing module.

### General Principles
- Keep things in one function unless genuinely reusable.
- Do not extract single-use helpers; inline at the call site.
- Avoid `try`/`except` where possible; use conditional checks.
- Prefer `const`-style (no reassignment); use ternaries or early returns.
- Avoid `else` after `return`/`raise`; use early returns.

### Imports
- Never use `import *`.
- Never alias imports (`import foo as bar`).
- Group imports: stdlib → third-party → local.

### SQLAlchemy Models
- Use snake_case for model field names matching column names.
- Use `Mapped[type]` with `mapped_column()`.
- Prefer ULID-based string IDs over auto-increment ints.

### Async
- Use `async with` for resource management.
- Use `asyncio.TaskGroup` (3.11+) for concurrent tool execution.
- Use `asyncio.Semaphore` for concurrency limiting when needed.

### Naming
- Modules: `snake_case.py`
- Classes: `PascalCase`
- Functions/methods: `snake_case`
- Constants: `UPPER_SNAKE_CASE`

### Comments
- No comments for obvious assignments or control flow.
- Add comments only for non-obvious constraints and surprising behavior.

---

## Workflow

1. **Read the docs first.** Before writing any code, read `docs/python-agent-development-plan.md` and `docs/architecture-analysis.md`.
2. **Read the reference source.** Study `docs/参考源码/opencode/` for understanding the original architecture.
3. **Respect ownership.** Do not implement the human's modules unless explicitly asked.
4. **Create stubs.** When the human hasn't implemented their module yet, create stub files so the agent's modules can import and work.
5. **Write tests.** Every agent-implemented module should have tests in `tests/`.
6. **Run ruff.** After writing code, run `ruff check src/` to verify.

---

## Communication Language

**The AI agent MUST respond in Chinese (中文).** All communication with the user, including code explanations, summaries, and progress updates, should be in Chinese.

---

## Commands

```bash
# Install dependencies (using uv)
uv sync

# Lint
uv run ruff check src/

# Type check
uv run mypy src/

# Run tests
uv run pytest tests/ -v
```
