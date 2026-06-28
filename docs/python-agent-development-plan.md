# Python OpenCode Agent — 详细开发文档

> 基于 OpenCode (TypeScript/Effect-TS) 架构分析，使用 Python/asyncio 完整复刻

---

## 0. 技术选型总览

| 维度 | OpenCode (TS) | Python 复刻 | 理由 |
|---|---|---|---|
| **异步运行时** | Effect-TS (Fiber/Scope/Layer) | asyncio + TaskGroup (3.11+) | 标准库，生态最广 |
| **依赖注入** | Effect Layer | 构造器注入 + 简单 Service Locator | 避免引入重量级 DI 框架 |
| **LLM 客户端** | `@opencode-ai/llm` 原生客户端 | 直调 OpenAI/Anthropic SDK | 用户指定，减少抽象层 |
| **存储** | Drizzle ORM + SQLite WAL | SQLAlchemy 2.0 async + aiosqlite | 最成熟的 Python ORM |
| **消息序列化** | 自研事件溯源 | JSON 列存储 + SQLAlchemy | 简单可靠 |
| **并发控制** | FiberSet + run-coordinator | asyncio.TaskGroup + asyncio.Queue FIFO | 结构化并发 |
| **Prompt 管理** | .txt 模板 + 模板变量 | Jinja2 模板 | Python 生态标准 |
| **CLI 框架** | 自研 | Click 或 Typer | 成熟稳定 |
| **配置管理** | JSONC/TS 文件 | YAML + Pydantic | 类型安全 |
| **日志** | Effect Logging | structlog | 结构化日志 |

---

## 1. 总体架构

```
                        ┌──────────────────────────────┐
                        │          CLI / API            │
                        │    (Typer / FastAPI 可选)     │
                        └──────────────┬───────────────┘
                                       │
                        ┌──────────────▼───────────────┐
                        │        Agent Runtime          │
                        │  ┌─────────────────────────┐ │
                        │  │     Agent Registry       │ │
                        │  │  (7 built-in + dynamic)  │ │
                        │  └─────────────────────────┘ │
                        │  ┌─────────────────────────┐ │
                        │  │   Permission System      │ │
                        │  └─────────────────────────┘ │
                        └──────────────┬───────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
    ┌─────────▼────────┐   ┌──────────▼──────────┐   ┌─────────▼────────┐
    │  Session Manager │   │    Prompt Engine     │   │   Agent Loop     │
    │  (CRUD + Life-   │   │  (Template → System  │   │  (run + turn +   │
    │   cycle + Fork)  │   │   + Env + Skills)    │   │   tool settle)   │
    └─────────┬────────┘   └──────────┬──────────┘   └─────────┬────────┘
              │                        │                        │
              └────────────────────────┼────────────────────────┘
                                       │
         ┌─────────────────────────────┼─────────────────────────────┐
         │                             │                             │
  ┌──────▼──────┐   ┌──────────┐  ┌────▼─────┐  ┌──────────┐  ┌─────▼─────┐
  │   Tool      │   │   MCP    │  │ Context  │  │ Provider │  │  Memory   │
  │  Registry   │   │  Client  │  │  Engine  │  │  Layer   │  │  (SQLite) │
  │ (15 built-  │   │ (OAuth + │  │ (Compac- │  │ (Catalog │  │           │
  │  in tools)  │   │  Stream) │  │  tion)   │  │ + Auth)  │  │           │
  └─────────────┘   └──────────┘  └──────────┘  └──────────┘  └───────────┘
```

## 2. 核心数据模型 (SQLAlchemy)

### 2.1 Session 表

```python
# opencode/models/session.py
from sqlalchemy import String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
import ulid

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(
        String(32), primary_key, default=lambda: f"ses_{ulid.new()}"
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("sessions.id"), nullable=True, index=True
    )
    agent: Mapped[str] = mapped_column(String(64), default="build")
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="idle"
    )  # idle | busy | compact
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # relations
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    children: Mapped[list["Session"]] = relationship(
        back_populates="parent", remote_side=[id], cascade="all, delete-orphan"
    )
    parent: Mapped["Session | None"] = relationship(
        back_populates="children", remote_side=[parent_id]
    )
```

### 2.2 Message 表 (事件溯源风格)

```python
# opencode/models/message.py
class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        String(32), primary_key, default=lambda: f"msg_{ulid.new()}"
    )
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id"), index=True
    )
    role: Mapped[str] = mapped_column(
        String(16)
    )  # user | assistant | system
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("messages.id"), nullable=True
    )

    # --- event-sourced parts (JSON array) ---
    parts: Mapped[str] = mapped_column(Text, default="[]")
    # parts JSON format:
    # [
    #   {"type":"text","text":"..."},
    #   {"type":"tool_call","tool_call_id":"...","tool_name":"...","args":{...}},
    #   {"type":"tool_result","tool_call_id":"...","output":"...","is_error":false},
    #   {"type":"reasoning","text":"..."}
    # ]

    # --- metadata ---
    finish_reason: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # stop | tool_calls | length | compact
    usage: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON: {input_tokens, output_tokens, cache_read_tokens, cost}

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    compacted: Mapped[bool] = mapped_column(default=False)  # overwritten by compaction

    session: Mapped["Session"] = relationship(back_populates="messages")
```

### 2.3 Compaction 摘要表

```python
class CompactionSummary(Base):
    __tablename__ = "compaction_summaries"

    id: Mapped[str] = mapped_column(
        String(32), primary_key, default=lambda: f"comp_{ulid.new()}"
    )
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id"), index=True
    )
    first_message_id: Mapped[str] = mapped_column(String(32))
    last_message_id: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)  # LLM 生成的摘要
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

### 2.4 Agent 配置表 (持久化动态 Agent)

```python
class AgentConfig(Base):
    __tablename__ = "agent_configs"

    name: Mapped[str] = mapped_column(String(64), primary_key)
    mode: Mapped[str] = mapped_column(String(16))  # primary | subagent | all
    hidden: Mapped[bool] = mapped_column(default=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    temperature: Mapped[float | None] = mapped_column(nullable=True)
    top_p: Mapped[float | None] = mapped_column(nullable=True)
    max_steps: Mapped[int | None] = mapped_column(nullable=True)
    permission_config: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

---

## 3. Agent System (代理系统)

### 3.1 Agent 定义

```python
# opencode/agent/agent.py
from dataclasses import dataclass, field
from typing import Literal, Optional
from opencode.agent.permissions import PermissionRuleset

@dataclass
class AgentInfo:
    """Agent 定义 —— 对应 OpenCode 的 Agent.Info"""
    name: str
    mode: Literal["primary", "subagent", "all"]
    native: bool = True
    hidden: bool = False
    permission: PermissionRuleset = field(default_factory=PermissionRuleset.default)
    # 可选模型覆盖
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    # Prompt 定制
    system_prompt: Optional[str] = None  # 自定义 system prompt
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_steps: Optional[int] = None  # None = 无限制
```

### 3.2 7 种内置 Agent

```python
# opencode/agent/builtins.py

# 1) build — 默认 primary agent，全部工具可用
BUILD_AGENT = AgentInfo(
    name="build",
    mode="primary",
    permission=PermissionRuleset.all(),
)

# 2) plan — 只读 + plan 文件可写
PLAN_AGENT = AgentInfo(
    name="plan",
    mode="primary",
    permission=PermissionRuleset(read=True, write_plan=True),
)

# 3) general — 通用子代理
GENERAL_AGENT = AgentInfo(
    name="general",
    mode="subagent",
    permission=PermissionRuleset.default(),
)

# 4) explore — 代码搜索子代理（只能用只读工具）
EXPLORE_AGENT = AgentInfo(
    name="explore",
    mode="subagent",
    permission=PermissionRuleset(read_only=True),
)

# 5) compaction — 上下文压缩（hidden）
COMPACTION_AGENT = AgentInfo(
    name="compaction",
    mode="primary",
    hidden=True,
    permission=PermissionRuleset(model_only=True),  # 只调 LLM，不调工具
    system_prompt="你是一个对话摘要专家...",
)

# 6) title — 标题生成（hidden）
TITLE_AGENT = AgentInfo(
    name="title",
    mode="primary",
    hidden=True,
    permission=PermissionRuleset(model_only=True),
)

# 7) summary — 对话摘要（hidden）
SUMMARY_AGENT = AgentInfo(
    name="summary",
    mode="primary",
    hidden=True,
    permission=PermissionRuleset(model_only=True),
)

BUILTIN_AGENTS = {
    "build": BUILD_AGENT,
    "plan": PLAN_AGENT,
    "general": GENERAL_AGENT,
    "explore": EXPLORE_AGENT,
    "compaction": COMPACTION_AGENT,
    "title": TITLE_AGENT,
    "summary": SUMMARY_AGENT,
}
```

### 3.3 Agent Registry

```python
# opencode/agent/registry.py
import asyncio
from opencode.agent.builtins import BUILTIN_AGENTS

class AgentRegistry:
    """Agent 注册表 —— 支持内置 + 动态生成 + 持久化"""

    def __init__(self, db_session_factory):
        self._db = db_session_factory
        self._lock = asyncio.Lock()

    async def get(self, name: str) -> AgentInfo | None:
        """按名称获取 Agent（先查内置，再查 DB）"""
        if name in BUILTIN_AGENTS:
            return BUILTIN_AGENTS[name]
        return await self._get_from_db(name)

    async def list(self, *, include_hidden: bool = False) -> list[AgentInfo]:
        """列出所有可用 Agent"""
        agents = list(BUILTIN_AGENTS.values())
        db_agents = await self._list_from_db()
        agents.extend(db_agents)
        if not include_hidden:
            agents = [a for a in agents if not a.hidden]
        return agents

    async def generate(self, description: str) -> AgentInfo:
        """
        动态 Agent 创建 —— 对应 OpenCode 的 generate()
        用 LLM generate_object 从自然语言生成 Agent 配置
        """
        prompt = PROMPT_GENERATE.format(description=description)
        response = await self._llm.generate_object(
            prompt=prompt,
            schema=AgentGenerationSchema,
        )
        info = AgentInfo(
            name=response.identifier,
            mode="subagent",
            system_prompt=response.system_prompt,
        )
        await self._save_to_db(info)
        return info

    async def _get_from_db(self, name: str) -> AgentInfo | None:
        ...
    async def _list_from_db(self) -> list[AgentInfo]:
        ...
    async def _save_to_db(self, info: AgentInfo):
        ...

# Prompt 模板 —— 对应 OpenCode 的 PROMPT_GENERATE
PROMPT_GENERATE = """\
You are an agent configuration generator. Given a task description,
output a JSON with:
- identifier: a unique kebab-case name for this agent
- whenToUse: when this agent should be invoked
- systemPrompt: the system prompt for this specialized agent

Task description: {description}
"""
```

### 3.4 Permission System

```python
# opencode/agent/permissions.py
from dataclasses import dataclass, field

@dataclass
class PermissionRuleset:
    """权限规则集 —— 对应 OpenCode 的 PermissionV1.Ruleset"""
    # 按名称允许/拒绝的工具
    allow_tools: set[str] = field(default_factory=set)
    deny_tools: set[str] = field(default_factory=set)
    # 高级权限
    allow_bash: bool = False
    allow_network: bool = False
    allow_file_write: bool = False
    allow_file_write_patterns: list[str] = field(default_factory=list)
    # MCP 工具
    allow_mcp_tools: set[str] = field(default_factory=set)

    @classmethod
    def all(cls) -> "PermissionRuleset":
        """全部权限 —— build agent"""
        return cls(
            allow_bash=True,
            allow_network=True,
            allow_file_write=True,
        )

    @classmethod
    def default(cls) -> "PermissionRuleset":
        """默认权限 —— general subagent"""
        return cls(
            allow_bash=True,
            allow_network=True,
            allow_file_write=True,
            deny_tools={"task", "skill"},  # subagent 不能创建 subagent
        )

    @classmethod
    def read_only(cls) -> "PermissionRuleset":
        """只读 —— explore subagent"""
        return cls(
            allow_tools={"read", "glob", "grep", "lsp"},
            allow_bash=False,
            allow_network=True,  # webfetch/websearch 需要
            allow_file_write=False,
        )

    @classmethod
    def model_only(cls) -> "PermissionRuleset":
        """只能调 LLM，不能调工具 —— compaction/title/summary"""
        return cls(
            deny_tools={"*"},  # 拒绝所有工具
        )

    @classmethod
    def subagent(cls) -> "PermissionRuleset":
        """子 agent 权限 — 禁止创建孙 agent 和提问。

        对应 Claude Code 的 ALL_AGENT_DISALLOWED_TOOLS:
        - task: 禁止递归创建孙 agent
        - question: 异步后台 agent 不能弹窗
        """
        return cls(
            allow_bash=True,
            allow_network=True,
            allow_file_write=True,
            deny_tools={"task", "question"},
        )

    def can_use(self, tool_name: str) -> bool:
        if "*" in self.deny_tools or tool_name in self.deny_tools:
            return False
        if tool_name in self.allow_tools:
            return True
        return self.allow_bash or self.allow_file_write  # fallback
```

---

## 4. Session Management (会话管理)

### 4.1 Session Service

```python
# opencode/session/session.py
import ulid
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from opencode.models.session import Session
from opencode.models.message import Message

class SessionService:
    """Session CRUD + 生命周期管理"""

    def __init__(self, db_factory):
        self.db = db_factory

    # ---- CRUD ----

    async def create(
        self,
        *,
        parent_id: str | None = None,
        agent: str = "build",
        provider_id: str | None = None,
        model_id: str | None = None,
        title: str | None = None,
    ) -> Session:
        """创建新 session"""
        async with self.db() as db:
            session = Session(
                id=f"ses_{ulid.new()}",
                parent_id=parent_id,
                agent=agent,
                provider_id=provider_id,
                model_id=model_id,
                title=title,
            )
            db.add(session)
            await db.commit()
            return session

    async def fork(self, session_id: str) -> Session:
        """
        Fork session —— 复制消息到新 ID
        对应 OpenCode prompt.ts:733
        """
        async with self.db() as db:
            original = await db.get(Session, session_id)
            new_session = Session(
                id=f"ses_{ulid.new()}",
                parent_id=original.parent_id,
                agent=original.agent,
                provider_id=original.provider_id,
                model_id=original.model_id,
                title=f"{original.title} (fork)",
            )
            db.add(new_session)

            # 复制所有消息
            messages = await db.execute(
                select(Message).where(Message.session_id == session_id)
            )
            for msg in messages.scalars():
                new_msg = Message(
                    id=f"msg_{ulid.new()}",
                    session_id=new_session.id,
                    role=msg.role,
                    parts=msg.parts,
                )
                db.add(new_msg)

            await db.commit()
            return new_session

    async def remove(self, session_id: str):
        """
        删除 session —— 递归取消子 session
        对应 OpenCode prompt.ts remove()
        """
        async with self.db() as db:
            # 递归找所有子 session
            children = await db.execute(
                select(Session).where(Session.parent_id == session_id)
            )
            for child in children.scalars():
                await self.remove(child.id)  # 递归
            # 删除消息 → 删除 session
            await db.execute(
                delete(Message).where(Message.session_id == session_id)
            )
            await db.execute(
                delete(Session).where(Session.id == session_id)
            )
            await db.commit()

    async def set_status(self, session_id: str, status: str):
        """更新 session 状态"""
        async with self.db() as db:
            session = await db.get(Session, session_id)
            session.status = status
            await db.commit()

    # ---- 消息操作 ----

    async def create_message(
        self,
        session_id: str,
        role: str,
        parts: list[dict],
        parent_id: str | None = None,
    ) -> Message:
        """创建新消息（事件溯源风格）"""
        async with self.db() as db:
            msg = Message(
                id=f"msg_{ulid.new()}",
                session_id=session_id,
                role=role,
                parts=json.dumps(parts),
                parent_id=parent_id,
            )
            db.add(msg)
            await db.commit()
            return msg

    async def append_part(self, message_id: str, part: dict):
        """追加 part 到已有消息（流式 tool result）"""
        async with self.db() as db:
            msg = await db.get(Message, message_id)
            parts = json.loads(msg.parts)
            parts.append(part)
            msg.parts = json.dumps(parts)
            await db.commit()

    async def update_message(self, message_id: str, **kwargs):
        """更新消息（finish_reason, usage 等）"""
        async with self.db() as db:
            msg = await db.get(Message, message_id)
            for key, value in kwargs.items():
                setattr(msg, key, value)
            await db.commit()

    async def get_messages(
        self, session_id: str, *, include_compacted: bool = False
    ) -> list[Message]:
        """获取 session 的所有消息"""
        async with self.db() as db:
            q = (
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at)
            )
            if not include_compacted:
                q = q.where(Message.compacted == False)
            result = await db.execute(q)
            return list(result.scalars())
```

### 4.2 并发控制 (Run Coordinator)

```python
# opencode/session/coordinator.py
import asyncio

class RunCoordinator:
    """
    FIFO 序列化执行 —— 对应 OpenCode run-coordinator.ts
    确保同一 session 的 prompt() 调用按序执行
    """

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, session_id: str) -> None:
        """获取 session 执行权"""
        if session_id in self._locks:
            await self._locks[session_id].acquire()
        else:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
            await lock.acquire()

    def release(self, session_id: str):
        """释放 session 执行权"""
        lock = self._locks.get(session_id)
        if lock and lock.locked():
            lock.release()

    async def run_exclusive(self, session_id: str, coro):
        """在排他锁内执行"""
        await self.acquire(session_id)
        try:
            return await coro
        finally:
            self.release(session_id)
```

---

## 5. Agent Loop (核心循环)

是整个系统的核心，对应 OpenCode 的 `prompt.ts:1184-1437`（V1）和 `runner/llm.ts`（V2）。

### 5.1 主循环

```python
# opencode/session/runner.py
import asyncio
import json
from enum import Enum
from opencode.session.system import build_system_prompt
from opencode.tool.registry import ToolRegistry
from opencode.agent.registry import AgentRegistry

class LoopResult(Enum):
    STOP = "stop"        # 正常结束（finish_reason=stop）
    COMPACT = "compact"  # 需要压缩后继续
    CONTINUE = "continue"

@dataclass
class SessionContext:
    """session 运行上下文"""
    session_id: str
    agent_name: str
    provider_id: str
    model_id: str
    max_steps: int | None
    permission: PermissionRuleset

class AgentLoop:
    """Agent 主循环"""

    def __init__(
        self,
        session_service: SessionService,
        agent_registry: AgentRegistry,
        tool_registry: ToolRegistry,
        provider_factory,          # → ProviderClient
        compaction_service,        # → CompactionService
        system_prompt_engine,      # → SystemPromptEngine
        coordinator: RunCoordinator,
    ):
        self.sessions = session_service
        self.agents = agent_registry
        self.tools = tool_registry
        self.provider_factory = provider_factory
        self.compaction = compaction_service
        self.system_prompt = system_prompt_engine
        self.coordinator = coordinator

    async def run(self, ctx: SessionContext) -> str:
        """运行 agent loop 直到结束"""
        return await self.coordinator.run_exclusive(
            ctx.session_id, self._run_loop(ctx)
        )

    async def _run_loop(self, ctx: SessionContext) -> str:
        step = 0
        agent = await self.agents.get(ctx.agent_name)
        max_steps = ctx.max_steps or agent.max_steps

        while True:
            # 1. 加载消息
            messages = await self.sessions.get_messages(ctx.session_id)

            # 2. 检查退出条件
            last_msg = messages[-1] if messages else None
            if last_msg and last_msg.role == "assistant":
                if last_msg.finish_reason == "stop":
                    return last_msg.id  # 正常结束

            # 3. 步数限制
            step += 1
            if max_steps and step > max_steps:
                break

            # 4. Compaction 检查
            needs_compact = await self.compaction.check(ctx.session_id, messages)
            if needs_compact:
                await self.compaction.execute(ctx.session_id, messages)
                continue

            # 5. 构建请求
            system = await self.system_prompt.build(agent, ctx)
            tools = await self.tools.resolve_for_agent(agent)
            model_messages = await self._to_model_messages(messages)

            # 6. 获取 provider client
            provider = self.provider_factory.create(ctx.provider_id)

            # 7. 单轮: LLM stream + tool execution
            result = await self._run_turn(
                ctx=ctx,
                provider=provider,
                system=system,
                messages=model_messages,
                tools=tools,
                agent=agent,
            )

            if result == LoopResult.STOP:
                break
            elif result == LoopResult.COMPACT:
                await self.compaction.execute(ctx.session_id, messages)

        # 返回最后的 assistant message id
        messages = await self.sessions.get_messages(ctx.session_id)
        return messages[-1].id if messages else None
```

### 5.2 单轮执行 (Run Turn)

```python
    async def _run_turn(
        self,
        ctx: SessionContext,
        provider,
        system: str,
        messages: list[dict],
        tools: list[dict],
        agent: AgentInfo,
    ) -> LoopResult:
        """
        单轮 LLM 调用 + 工具执行
        对应 OpenCode runner/llm.ts runTurnAttempt()
        """
        # 1. 创建 assistant 消息（先占位）
        assistant_msg = await self.sessions.create_message(
            ctx.session_id, "assistant", parts=[]
        )

        # 2. 调用 LLM stream
        stream = await provider.stream(
            model=ctx.model_id,
            system=system,
            messages=messages,
            tools=tools,
            temperature=agent.temperature or 0.7,
            top_p=agent.top_p,
        )

        # 3. 处理流事件
        text_buffer = ""
        tool_calls: list[ToolCall] = []
        finish_reason = None

        async for event in stream:
            if event.type == "text_delta":
                text_buffer += event.text
                # 可选的实时输出回调
                # await self._on_text_delta(ctx, event.text)

            elif event.type == "tool_call_start":
                tool_calls.append(ToolCall(
                    id=event.tool_call_id,
                    name=event.tool_name,
                    args=event.args,
                ))

            elif event.type == "tool_call_delta":
                # 追加参数（流式 tool call args）
                tc = next(tc for tc in tool_calls if tc.id == event.tool_call_id)
                tc.args = event.args  # 累积的参数

            elif event.type == "finish":
                finish_reason = event.finish_reason
                # 记录 usage
                await self.sessions.update_message(
                    assistant_msg.id,
                    finish_reason=finish_reason,
                    usage=json.dumps(event.usage),
                )

        # 4. 更新 assistant 消息 parts
        parts = []
        if text_buffer:
            parts.append({"type": "text", "text": text_buffer})
        for tc in tool_calls:
            parts.append({
                "type": "tool_call",
                "tool_call_id": tc.id,
                "tool_name": tc.name,
                "args": tc.args,
            })
        await self.sessions.update_message(
            assistant_msg.id, parts=json.dumps(parts)
        )

        # 5. 执行工具（并行 FiberSet 风格）
        if tool_calls:
            tool_results = await self._settle_tools(
                ctx, agent, tool_calls
            )
            # 追加 tool result parts
            for tr in tool_results:
                await self.sessions.append_part(
                    assistant_msg.id, {
                        "type": "tool_result",
                        "tool_call_id": tr.call_id,
                        "output": tr.output,
                        "is_error": tr.is_error,
                    }
                )

            # 如果没有文本输出（纯工具调用），继续循环
            if not text_buffer or finish_reason == "tool_calls":
                return LoopResult.CONTINUE

        # 6. 根据 finish_reason 决定下一步
        if finish_reason == "stop":
            return LoopResult.STOP
        elif finish_reason == "length":
            return LoopResult.COMPACT
        else:
            return LoopResult.CONTINUE
```

### 5.3 工具并发执行 (FiberSet 等价物)

```python
    async def _settle_tools(
        self,
        ctx: SessionContext,
        agent: AgentInfo,
        tool_calls: list[ToolCall],
    ) -> list[ToolResult]:
        """
        并行执行所有工具调用
        对应 OpenCode FiberSet + settle_tool()
        """
        async def execute_one(tc: ToolCall) -> ToolResult:
            try:
                tool = self.tools.get(tc.name)
                if not tool:
                    return ToolResult(tc.id, "", is_error=True,
                                      error=f"Unknown tool: {tc.name}")

                # 权限检查
                if not agent.permission.can_use(tc.name):
                    return ToolResult(tc.id, "", is_error=True,
                                      error=f"Permission denied: {tc.name}")

                # 构建 ToolContext
                tool_ctx = ToolContext(
                    session_id=ctx.session_id,
                    agent=ctx.agent_name,
                    assistant_message_id=None,  # 在 tool 执行时可能还没有
                    tool_call_id=tc.id,
                    abort_signal=asyncio.Event(),
                )

                output = await tool.execute(tc.args, tool_ctx)
                # 截断输出
                output = truncate_output(output, max_tokens=10000)
                return ToolResult(tc.id, output, is_error=False)

            except Exception as e:
                return ToolResult(tc.id, str(e), is_error=True)

        # 并行执行所有工具
        return await asyncio.gather(*[execute_one(tc) for tc in tool_calls])
```

---

## 6. Tool System (工具系统)

### 6.1 Tool 基类

```python
# opencode/tool/tool.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

@dataclass
class ToolContext:
    """工具执行上下文 —— 对应 OpenCode V1 的富 context"""
    session_id: str
    agent: str
    assistant_message_id: str | None
    tool_call_id: str
    abort_signal: asyncio.Event
    # 回调：允许工具向用户提问（对应 ask()）
    ask_callback: Callable[..., Awaitable] | None = None

@dataclass
class ToolResult:
    """工具执行结果"""
    title: str | None = None
    output: str = ""
    metadata: dict = field(default_factory=dict)
    # 对于 task tool
    task_id: str | None = None

class Tool(ABC):
    """工具基类 —— 对应 OpenCode Tool.make()"""

    name: str
    description: str
    parameters: dict  # JSON Schema

    @abstractmethod
    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        """执行工具"""
        ...

    def to_openai_tool(self) -> dict:
        """转换为 OpenAI tool definition 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_tool(self) -> dict:
        """转换为 Anthropic tool use 格式"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
```

### 6.2 Tool Registry

```python
# opencode/tool/registry.py
class ToolRegistry:
    """工具注册表 —— 双注册表架构简化为单一注册表"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        """注册工具"""
        self._tools[tool.name] = tool

    def unregister(self, name: str):
        """注销工具"""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_all(self) -> list[Tool]:
        return list(self._tools.values())

    async def resolve_for_agent(self, agent: AgentInfo) -> list[dict]:
        """
        为指定 Agent 解析可用工具
        过滤权限 + MCP 动态工具注入
        """
        tools = []
        for name, tool in self._tools.items():
            if agent.permission.can_use(name):
                tools.append(tool.to_anthropic_tool())  # 或基于 provider
        return tools
```

### 6.3 内置工具清单

```python
# opencode/tool/builtins/__init__.py
"""
15 个内置工具 —— 对应 OpenCode tool/ 目录

read       — 读取文件
write      — 写入文件
edit       — 精确字符串替换编辑
bash       — 执行 shell 命令
glob       — 文件模式匹配
grep       — 内容搜索（ripgrep）
task       — 创建子代理任务
webfetch   — 获取 URL 内容
websearch  — 网页搜索
question   — 向用户提问
skill      — 加载 skill
todowrite  — 管理任务列表
apply_patch— 应用 patch
lsp        — LSP 集成
plan_exit  — 退出计划模式
"""
```

### 6.4 示例: Read Tool

```python
# opencode/tool/builtins/read.py
from pathlib import Path

class ReadTool(Tool):
    name = "read"
    description = "Reads a file from the local filesystem..."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from",
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read",
            },
        },
        "required": ["file_path"],
    }

    def __init__(self, workspace_root: str):
        self.workspace = Path(workspace_root)

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        file_path = Path(params["file_path"])
        offset = params.get("offset", 0)
        limit = params.get("limit")

        # 安全检查
        if not file_path.is_absolute():
            file_path = self.workspace / file_path
        file_path = file_path.resolve()
        if not str(file_path).startswith(str(self.workspace)):
            return ToolResult(output="Access denied: path outside workspace")

        try:
            content = file_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            if offset:
                lines = lines[offset:]
            if limit:
                lines = lines[:limit]

            # 添加行号
            numbered = "\n".join(
                f"{i + offset + 1}\t{line}"
                for i, line in enumerate(lines)
            )
            return ToolResult(output=numbered)

        except FileNotFoundError:
            return ToolResult(output=f"File not found: {file_path}")
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}")
```

### 6.5 Task Tool (MultiAgent 通信核心)

基于 Claude Code 的 MultiAgent 架构实现。通信模型：**所有 agent 共用同一套 AgentLoop，差异全在 SessionContext 隔离**。

```python
# src/tool/task.py
class TaskTool(Tool):
    """
    子代理创建工具 — Claude Code 的 AgentTool 等价物。

    架构原则（来自 Claude Code 源码）：
    1. 同一套 AgentLoop — 差异在 createSubagentContext()
    2. Worker Context 精简 — 不传父历史，只给任务 spec
    3. 防递归 — 子 agent 工具箱不含 task/question
    4. 子→父通信 — <task-result> XML 单向注入
    """

    def __init__(self, sessions, agents, loop_factory, background_jobs,
                 router: ModelRouter, registry: ProviderRegistry):
        ...

    async def execute(self, params, ctx) -> ToolResult:
        # 1. ModelRouter 选择模型（能力匹配 + cost ≤ 父模型）
        # 2. 创建子 session (parent_id=父session_id)
        # 3. 构建 Worker Context — 精简：只含 task prompt + system_prompt
        # 4. PermissionRuleset.subagent() — 禁止 task/question
        # 5. 执行 loop.run()（父子用同一 AgentLoop）
        # 6. 返回 <task-result> XML

# 通信协议 — 子→父 XML 注入（对应 Claude Code task-notification）
# <task-result>
#   <agent>explore</agent>
#   <status>completed|failed|stopped</status>
#   <result>具体结果文本</result>
#   <usage total_tokens="1234" tool_calls="3" />
#   <duration_ms>4567</duration_ms>
# </task-result>

# 工具锁定 — PermissionRuleset.subagent()
# 子 agent 不能调用:
#   - task（防递归创建孙 agent）
#   - question（后台 agent 不能弹窗）

# Worker Context 精简原则（对应 Claude Code createSubagentContext）:
#   父 agent: messages(100+), readFileState(50文件), depth=0
#                ↓ 隔离后 ↓
#   子 agent: messages(1条), readFileState(全新), depth=1
#             看不到父历史, 不共享缓存, 身份独立
```

**与 Claude Code 的对照**：

| 维度 | Claude Code | SunshineAgent |
|------|------------|---------------|
| Agent Loop | 共用 query()/queryLoop() | 共用 AgentLoop |
| 上下文隔离 | createSubagentContext() 20+ 字段逐字段决策 | SessionContext 隔离 |
| Worker 上下文 | 只给 spec + system_prompt | 同，_build_worker_context() |
| 防递归 | ALL_AGENT_DISALLOWED_TOOLS | PermissionRuleset.subagent() |
| 子→父通信 | `<task-notification>` XML 注入 | `<task-result>` XML 注入 |
| 模型选择 | Agent.model ?? 父 model | ModelRouter 能力路由 |
| Resume/Continue | transcript 持久化 | ❌ 未实现（后续） |
| Coordinator 模式 | 370 行专用 prompt | ❌ 未实现（后续） |
| Fork (Cache) | 字节级 prompt 匹配 | ❌ 未实现（后续） |


---

## 7. Background Job System (后台任务)

```python
# opencode/background/job.py
import asyncio
from dataclasses import dataclass
from enum import Enum

class JobStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class Job:
    id: str
    session_id: str
    task: asyncio.Task
    status: JobStatus = JobStatus.ACTIVE
    result: str | None = None
    error: str | None = None

class BackgroundJobManager:
    """后台 Job 管理器 —— 对应 OpenCode job.ts"""

    def __init__(self):
        self._jobs: dict[str, Job] = {}

    async def start(self, session_id: str, coro) -> Job:
        """启动后台任务"""
        task = asyncio.create_task(coro)
        job = Job(
            id=f"job_{ulid.new()}",
            session_id=session_id,
            task=task,
        )
        self._jobs[job.id] = job

        # 完成后更新状态
        def done_callback(t: asyncio.Task):
            if t.exception():
                job.status = JobStatus.FAILED
                job.error = str(t.exception())
            else:
                job.status = JobStatus.COMPLETED
                job.result = str(t.result()) if t.result() else None

        task.add_done_callback(done_callback)
        return job

    async def wait(self, job_id: str, timeout: float | None = None) -> str | None:
        """等待 job 完成"""
        job = self._jobs[job_id]
        try:
            return await asyncio.wait_for(job.task, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def promote(self, job_id: str):
        """将 job 转为后台运行（解除当前 wait）"""
        job = self._jobs[job_id]
        job.status = JobStatus.ACTIVE

    async def cancel(self, job_id: str):
        """取消 job"""
        job = self._jobs[job_id]
        job.task.cancel()
        job.status = JobStatus.CANCELLED

    async def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)
```

---

## 8. Prompt Engine (提示词引擎)

### 8.1 System Prompt 组装

```python
# opencode/session/system.py
from jinja2 import Environment, FileSystemLoader

class SystemPromptEngine:
    """
    System prompt 组装 —— 对应 OpenCode system.ts
    Pipeline: base → agent_prompt → environment → skills → user.system
    """

    def __init__(self, templates_dir: str):
        self.jinja = Environment(loader=FileSystemLoader(templates_dir))
        self._model_routes = {
            r"claude": "anthropic",
            r"gpt-4|o1|o3": "beast",
            r"gpt.*codex": "codex",
            r"gpt": "gpt",
            r"gemini": "gemini",
            r"trinity": "trinity",
            "*": "default",
        }

    def select_template(self, model_id: str) -> str:
        """按模型路由选择模板 —— 对应 system.ts:24-37"""
        import re
        model_lower = model_id.lower()
        for pattern, name in self._model_routes.items():
            if pattern == "*":
                return f"{name}.txt"
            if re.search(pattern, model_lower):
                return f"{name}.txt"
        return "default.txt"

    async def build(
        self,
        agent: AgentInfo,
        ctx: SessionContext,
    ) -> str:
        """
        组装完整 system prompt
        对应 OpenCode prompt pipeline:
        base | agent_prompt → environment → skills → user.system
        """
        template_name = self.select_template(ctx.model_id)
        template = self.jinja.get_template(template_name)

        # 基础 system prompt
        base = template.render(
            model=ctx.model_id,
            provider=ctx.provider_id,
            date=datetime.now().strftime("%Y-%m-%d"),
        )

        # Agent 自定义 prompt
        agent_prompt = agent.system_prompt or ""

        # 环境信息
        env = await self._build_environment()

        # Skills
        skills = await self._build_skills_xml()

        # 拼接
        parts = [base, agent_prompt, env, skills]
        return "\n\n".join(p for p in parts if p)

    async def _build_environment(self) -> str:
        """构建 <env> 块 —— 对应 system.ts environment()"""
        import platform, os
        return f"""\
<env>
  Platform: {platform.system()} {platform.release()}
  Shell: {os.environ.get('SHELL', 'bash')}
  Workspace: {os.getcwd()}
  Date: {datetime.now().strftime('%Y-%m-%d')}
</env>"""

    async def _build_skills_xml(self) -> str:
        """构建 <available_skills> XML"""
        # 从 .claude/skills/ 或其他位置加载 skill
        ...
```

### 8.2 消息转换 (History → Model Messages)

```python
# opencode/session/message_convert.py

def to_model_messages(
    messages: list[Message],
    provider_id: str,
) -> list[dict]:
    """
    将 DB 中的消息列表转换为 LLM API 格式
    对应 OpenCode message-v2.ts toModelMessages()

    处理：
    - 合并连续的 user/tool_result 消息
    - 填充 assistant 消息的 tool_calls
    - provider 特定的格式变换
    """
    result = []
    for msg in messages:
        parts = json.loads(msg.parts or "[]")

        if provider_id == "openai":
            converted = _to_openai_format(msg.role, parts)
        elif provider_id == "anthropic":
            converted = _to_anthropic_format(msg.role, parts)
        else:
            converted = _to_generic_format(msg.role, parts)

        if converted:
            result.append(converted)

    return result

def _to_openai_format(role: str, parts: list[dict]) -> dict:
    """转为 OpenAI Chat Completions 格式"""
    content = []
    tool_calls = []

    for p in parts:
        if p["type"] == "text":
            content.append({"type": "text", "text": p["text"]})
        elif p["type"] == "tool_call":
            tool_calls.append({
                "id": p["tool_call_id"],
                "type": "function",
                "function": {
                    "name": p["tool_name"],
                    "arguments": json.dumps(p["args"]),
                },
            })
        elif p["type"] == "tool_result":
            # OpenAI 中 tool result 是独立消息，role=tool
            return {
                "role": "tool",
                "tool_call_id": p["tool_call_id"],
                "content": p["output"],
            }

    msg = {"role": role, "content": content or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg

def _to_anthropic_format(role: str, parts: list[dict]) -> dict:
    """转为 Anthropic Messages API 格式"""
    content = []

    for p in parts:
        if p["type"] == "text":
            content.append({"type": "text", "text": p["text"]})
        elif p["type"] == "tool_call":
            content.append({
                "type": "tool_use",
                "id": p["tool_call_id"],
                "name": p["tool_name"],
                "input": p["args"],
            })
        elif p["type"] == "tool_result":
            content.append({
                "type": "tool_result",
                "tool_use_id": p["tool_call_id"],
                "content": p["output"],
                "is_error": p.get("is_error", False),
            })

    return {"role": role, "content": content}
```

### 8.3 Prompt 模板文件

```
prompts/
├── anthropic.txt     # Claude 模型专用
├── beast.txt         # GPT-4/o1/o3 专用（自主 Agent 风格）
├── codex.txt         # GPT Codex 专用
├── gpt.txt           # 其他 GPT 模型
├── gemini.txt        # Gemini 模型
├── trinity.txt       # Trinity 模型
├── default.txt       # 默认模板
└── generate_agent.txt # Agent 生成模板
```

---

## 9. Provider Layer (模型供应商层)

### 9.1 ProviderClient 接口

```python
# opencode/provider/base.py
from abc import ABC, abstractmethod
from typing import AsyncIterator

@dataclass
class StreamEvent:
    type: str  # text_delta | tool_call_start | tool_call_delta | finish
    text: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    args: str | None = None  # JSON string, 流式累积
    finish_reason: str | None = None
    usage: dict | None = None

class ProviderClient(ABC):
    """模型供应商客户端基类"""

    provider_id: str  # "openai" | "anthropic"

    @abstractmethod
    async def stream(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.7,
        top_p: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式调用 LLM，返回统一 StreamEvent"""
        ...

    @abstractmethod
    async def generate_object(
        self,
        model: str,
        system: str,
        prompt: str,
        schema: dict,
    ) -> dict:
        """结构化输出 (JSON Schema) —— 用于 Agent 生成、DAG 生成"""
        ...
```

### 9.2 Anthropic Client

```python
# opencode/provider/anthropic_client.py
from anthropic import AsyncAnthropic

class AnthropicClient(ProviderClient):
    provider_id = "anthropic"

    def __init__(self, api_key: str | None = None):
        self.client = AsyncAnthropic(api_key=api_key)

    async def stream(self, model, system, messages, tools, **kwargs):
        # 转换 tool definitions
        anthropic_tools = tools  # 在 registry 中已经是 anthropic 格式

        async with self.client.messages.stream(
            model=model,
            system=system,
            messages=messages,
            tools=anthropic_tools,
            max_tokens=kwargs.get("max_tokens", 16384),
            temperature=kwargs.get("temperature", 0.7),
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    yield StreamEvent(type="text_delta", text=event.text)
                elif event.type == "tool_use":
                    yield StreamEvent(
                        type="tool_call_start",
                        tool_call_id=event.id,
                        tool_name=event.name,
                        args=json.dumps(event.input),
                    )
                elif event.type == "message_stop":
                    yield StreamEvent(
                        type="finish",
                        finish_reason="stop",
                        usage={
                            "input_tokens": stream.usage.input_tokens,
                            "output_tokens": stream.usage.output_tokens,
                        },
                    )

    async def generate_object(self, model, system, prompt, schema):
        """Anthropic 不支持原生 JSON Schema，用 prompt engineering"""
        from anthropic.types import ToolParam
        response = await self.client.messages.create(
            model=model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[ToolParam(
                name="generate",
                description="Generate structured output",
                input_schema=schema,
            )],
            tool_choice={"type": "tool", "name": "generate"},
            max_tokens=4096,
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        return {}
```

### 9.3 OpenAI Client

```python
# opencode/provider/openai_client.py
from openai import AsyncOpenAI

class OpenAIClient(ProviderClient):
    provider_id = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def stream(self, model, system, messages, tools, **kwargs):
        # 转换 tool definitions 为 OpenAI 格式
        openai_tools = [t["function"] for t in tools] if tools else None

        stream = await self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}] + messages,
            tools=openai_tools,
            temperature=kwargs.get("temperature", 0.7),
            top_p=kwargs.get("top_p"),
            max_tokens=kwargs.get("max_tokens"),
            stream=True,
        )

        tool_call_buffer: dict[int, dict] = {}  # index → accumulated

        async for chunk in stream:
            delta = chunk.choices[0].delta

            if delta.content:
                yield StreamEvent(type="text_delta", text=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_buffer:
                        tool_call_buffer[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name if tc.function else "",
                            "args": "",
                        }
                    buf = tool_call_buffer[idx]
                    if tc.id:
                        buf["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            buf["name"] = tc.function.name
                        if tc.function.arguments:
                            buf["args"] += tc.function.arguments

            if chunk.choices[0].finish_reason:
                # 输出所有累积的 tool calls
                for buf in tool_call_buffer.values():
                    yield StreamEvent(
                        type="tool_call_start",
                        tool_call_id=buf["id"],
                        tool_name=buf["name"],
                        args=buf["args"],
                    )
                yield StreamEvent(
                    type="finish",
                    finish_reason=chunk.choices[0].finish_reason,
                    usage=chunk.usage.model_dump() if chunk.usage else None,
                )

    async def generate_object(self, model, system, prompt, schema):
        """OpenAI 原生支持 JSON Schema structured output"""
        response = await self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema},
            },
            temperature=0.1,
        )
        return json.loads(response.choices[0].message.content)
```

### 9.4 Model Catalog

```python
# opencode/provider/catalog.py

@dataclass
class ModelEntry:
    """模型注册项 —— 对应 OpenCode models-dev.ts"""
    model_id: str
    provider_id: str
    display_name: str
    # 能力
    context_window: int
    max_output_tokens: int
    supports_tools: bool = True
    supports_images: bool = False
    supports_streaming: bool = True
    # 定价 (per 1M tokens)
    input_price: float = 0
    output_price: float = 0
    cache_read_price: float = 0
    # 标签
    tags: list[str] = field(default_factory=list)
    # 变体
    reasoning_effort: str | None = None

class ModelCatalog:
    """模型目录 —— 静态配置 + 可选动态拉取"""

    # 静态模型注册（核心模型）
    MODELS: list[ModelEntry] = [
        ModelEntry(
            model_id="claude-sonnet-4-6",
            provider_id="anthropic",
            display_name="Claude Sonnet 4.6",
            context_window=200000,
            max_output_tokens=16384,
            supports_images=True,
            input_price=3.0,
            output_price=15.0,
            cache_read_price=0.30,
            tags=["code_generation", "reasoning", "planning", "general"],
        ),
        ModelEntry(
            model_id="claude-opus-4-6",
            provider_id="anthropic",
            display_name="Claude Opus 4.6",
            context_window=200000,
            max_output_tokens=32768,
            supports_images=True,
            input_price=15.0,
            output_price=75.0,
            cache_read_price=1.50,
            tags=["planning", "architecture", "reasoning", "code_generation"],
        ),
        ModelEntry(
            model_id="gpt-5",
            provider_id="openai",
            display_name="GPT-5",
            context_window=128000,
            max_output_tokens=16384,
            supports_images=True,
            input_price=5.0,
            output_price=20.0,
            tags=["planning", "code_generation", "reasoning", "search"],
        ),
        # Qwen 系列（通过 OpenAI 兼容接口）
        ModelEntry(
            model_id="qwen3-8b",
            provider_id="openai",  # OpenAI 兼容
            display_name="Qwen3 8B",
            context_window=32768,
            max_output_tokens=8192,
            input_price=0.0,  # 本地部署
            output_price=0.0,
            tags=["code_generation", "search", "test"],
        ),
    ]

    def resolve(self, provider_id: str, model_id: str) -> ModelEntry | None:
        for m in self.MODELS:
            if m.provider_id == provider_id and m.model_id == model_id:
                return m
        return None

    def list_by_tag(self, tag: str) -> list[ModelEntry]:
        return [m for m in self.MODELS if tag in m.tags]

    def list_by_provider(self, provider_id: str) -> list[ModelEntry]:
        return [m for m in self.MODELS if m.provider_id == provider_id]
```

---

## 10. Context Engine (上下文引擎)

### 10.1 Token Estimation

```python
# opencode/context/token.py

def estimate_tokens(text: str) -> int:
    """
    简单 token 估算 —— 对应 OpenCode util/token.ts
    4 chars ≈ 1 token（英文），中文 1 char ≈ 1.5 token
    """
    if not text:
        return 0
    # 粗略估算
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return (ascii_chars // 4) + int(non_ascii_chars * 1.5)

def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算消息列表的总 token 数"""
    total = 0
    for msg in messages:
        if isinstance(msg.get("content"), str):
            total += estimate_tokens(msg["content"])
        elif isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict):
                    total += estimate_tokens(json.dumps(part))
    return total
```

### 10.2 Compaction Service

```python
# opencode/session/compaction.py

class CompactionService:
    """
    上下文压缩 —— 对应 OpenCode compaction.ts
    V2 风格：Head/Tail 分割 → LLM 生成摘要 → 插入 checkpoint
    """

    def __init__(self, provider_factory, sessions: SessionService):
        self.provider_factory = provider_factory
        self.sessions = sessions
        # 配置
        self.context_ratio = 0.8  # 上下文达到 80% 时触发压缩
        self.buffer_tokens = 4096  # 保留 buffer

    async def check(self, session_id: str, messages: list[Message]) -> bool:
        """
        检查是否需要压缩 —— 对应 compaction.ts estimate()
        条件: estimated_tokens > context_window - max(output, buffer)
        """
        total_estimated = sum(
            estimate_tokens(msg.parts or "") for msg in messages
        )
        # 获取当前模型的 context_window
        session = await self.sessions.get(session_id)
        model_entry = ...  # 从 catalog 查

        threshold = model_entry.context_window - max(
            model_entry.max_output_tokens, self.buffer_tokens
        )
        return total_estimated > threshold

    async def execute(self, session_id: str, messages: list[Message]):
        """
        执行压缩
        """
        # 1. Head/Tail 分割: 保留最近 N 条消息
        keep_last = max(3, len(messages) // 4)  # 保留后 25%
        head = messages[:-keep_last]
        tail = messages[-keep_last:]

        # 2. 用 compaction agent 生成摘要
        provider = self.provider_factory.create("anthropic")  # 或其他
        summary = await self._generate_summary(provider, head)

        # 3. 将 head 消息标记为 compacted
        for msg in head:
            await self.sessions.update_message(msg.id, compacted=True)

        # 4. 插入 checkpoint 消息
        await self.sessions.create_message(
            session_id,
            "system",
            parts=[{"type": "text", "text": f"<conversation-checkpoint>\n{summary}\n</conversation-checkpoint>"}],
        )

        # 5. 保存摘要记录
        async with self.sessions.db() as db:
            summary_record = CompactionSummary(
                id=f"comp_{ulid.new()}",
                session_id=session_id,
                first_message_id=head[0].id if head else "",
                last_message_id=head[-1].id if head else "",
                summary=summary,
            )
            db.add(summary_record)
            await db.commit()

    async def _generate_summary(self, provider, messages: list[Message]) -> str:
        """用 LLM 生成结构化摘要"""
        # 构建历史文本
        history_text = []
        for msg in messages:
            parts = json.loads(msg.parts or "[]")
            text = " ".join(p.get("text", "") for p in parts if p["type"] == "text")
            if text:
                history_text.append(f"[{msg.role}]: {text[:500]}...")  # 截断

        prompt = COMPACTION_PROMPT.format(
            history="\n".join(history_text),
        )

        response = await provider.stream(
            model="claude-haiku-4-5",  # 用便宜的模型做摘要
            system="You are a conversation summarizer.",
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            temperature=0.1,
        )

        result = []
        async for event in response:
            if event.type == "text_delta":
                result.append(event.text)
        return "".join(result)


# 摘要模板 —— 对应 OpenCode compaction prompt
COMPACTION_PROMPT = """\
Summarize the following conversation history. Include:
- Goal: What the user is trying to accomplish
- Constraints: Any constraints mentioned
- Progress: What has been accomplished so far
- Key Decisions: Important decisions made
- Next Steps: What remains to be done
- Critical Context: Any context that must not be lost
- Relevant Files: Files that have been examined or modified

Conversation:
{history}
"""
```

---

## 11. MCP Integration (MCP 集成)

```python
# opencode/mcp/client.py
import asyncio
import json
from dataclasses import dataclass
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

@dataclass
class MCPServerConfig:
    """MCP Server 配置"""
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)

class MCPClient:
    """
    MCP 客户端 —— 对应 OpenCode mcp/index.ts
    管理 MCP server 连接、工具发现、OAuth
    """

    def __init__(self):
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, dict] = {}  # server_name.tool_name → MCP tool def

    async def connect(self, config: MCPServerConfig):
        """连接到 MCP server"""
        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env,
        )

        async with stdio_client(server_params) as (read, write):
            session = ClientSession(read, write)
            await session.initialize()
            self._sessions[config.name] = session

            # 发现工具
            result = await session.list_tools()
            for tool in result.tools:
                full_name = f"mcp__{config.name}__{tool.name}"
                self._tools[full_name] = {
                    "name": full_name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                    "server": config.name,
                    "original_name": tool.name,
                }

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用 MCP 工具"""
        tool = self._tools.get(tool_name)
        if not tool:
            raise ValueError(f"Unknown MCP tool: {tool_name}")

        session = self._sessions[tool["server"]]
        result = await session.call_tool(tool["original_name"], arguments)
        return json.dumps(result.content)

    def list_tools(self) -> list[dict]:
        """列出所有 MCP 工具（用于注入 Tool Registry）"""
        return list(self._tools.values())

    async def disconnect_all(self):
        """断开所有连接"""
        for name, session in self._sessions.items():
            try:
                await session.close()
            except Exception:
                pass
        self._sessions.clear()
        self._tools.clear()
```

---

## 12. Skill System (技能系统)

```python
# opencode/skill/skill.py
from pathlib import Path
import frontmatter  # python-frontmatter

@dataclass
class Skill:
    name: str
    description: str
    path: str
    content: str
    # frontmatter 元数据
    model: str | None = None
    mode: str = "default"  # default | append

class SkillLoader:
    """
    Skill 加载器 —— 对应 OpenCode skill/index.ts
    从文件系统加载 .md skill 文件
    """

    def __init__(self, skill_dirs: list[str]):
        self.skill_dirs = [Path(d) for d in skill_dirs]
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    async def load_all(self):
        """扫描所有 skill 目录，加载 .md 文件"""
        for skill_dir in self.skill_dirs:
            if not skill_dir.exists():
                continue
            for md_file in skill_dir.glob("**/*.md"):
                await self._load_file(md_file)
        self._loaded = True

    async def _load_file(self, path: Path):
        post = frontmatter.load(path)
        name = path.stem
        self._skills[name] = Skill(
            name=name,
            description=post.get("description", name),
            path=str(path),
            content=post.content,
            model=post.get("model"),
            mode=post.get("mode", "default"),
        )

    def list_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def to_system_prompt(self) -> str:
        """将所有 skill 序列化为 system prompt 中的 XML"""
        skills = self.list_skills()
        if not skills:
            return ""

        lines = ["<available_skills>"]
        for skill in skills:
            lines.append(f"  <skill>")
            lines.append(f"    <name>{skill.name}</name>")
            lines.append(f"    <description>{skill.description}</description>")
            lines.append(f"    <location>{skill.path}</location>")
            lines.append(f"  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)
```

---

## 13. CLI 入口

```python
# opencode/cli.py
import asyncio
import typer
from pathlib import Path

app = typer.Typer()

@app.command()
def run(
    prompt: str = typer.Argument(..., help="The prompt to execute"),
    agent: str = typer.Option("build", help="Agent to use"),
    model: str = typer.Option("claude-sonnet-4-6", help="Model ID"),
    provider: str = typer.Option("anthropic", help="Provider ID"),
    workspace: Path = typer.Option(Path.cwd(), help="Workspace directory"),
):
    """Run a single prompt through the agent"""
    asyncio.run(_run_async(prompt, agent, model, provider, workspace))

async def _run_async(prompt, agent, model, provider, workspace):
    # 初始化所有服务
    db_engine = create_async_engine(
        f"sqlite+aiosqlite:///{workspace}/.opencode/state.db",
        echo=False,
    )
    await init_db(db_engine)

    session_factory = sessionmaker(db_engine, class_=AsyncSession)
    sessions = SessionService(session_factory)
    agents = AgentRegistry(session_factory)
    tools = ToolRegistry()
    provider_factory = ProviderFactory()
    coordinator = RunCoordinator()
    system_engine = SystemPromptEngine("prompts/")
    compaction = CompactionService(provider_factory, sessions)
    jobs = BackgroundJobManager()

    # 注册所有内置工具
    _register_builtin_tools(tools, sessions, agents, provider_factory, jobs)

    # 创建 AgentLoop
    loop = AgentLoop(
        session_service=sessions,
        agent_registry=agents,
        tool_registry=tools,
        provider_factory=provider_factory,
        compaction_service=compaction,
        system_prompt_engine=system_engine,
        coordinator=coordinator,
    )

    # 创建 session
    session = await sessions.create(
        agent=agent,
        provider_id=provider,
        model_id=model,
    )

    # 注入 user message
    await sessions.create_message(
        session.id, "user",
        parts=[{"type": "text", "text": prompt}],
    )

    # 运行
    ctx = SessionContext(
        session_id=session.id,
        agent_name=agent,
        provider_id=provider,
        model_id=model,
        max_steps=None,
        permission=PermissionRuleset.all(),
    )

    result_msg_id = await loop.run(ctx)

    # 输出结果
    messages = await sessions.get_messages(session.id)
    last = messages[-1] if messages else None
    if last:
        parts = json.loads(last.parts or "[]")
        for p in parts:
            if p["type"] == "text":
                typer.echo(p["text"])

@app.command()
def interactive(
    agent: str = typer.Option("build"),
    model: str = typer.Option("claude-sonnet-4-6"),
    provider: str = typer.Option("anthropic"),
):
    """Start interactive REPL session"""
    asyncio.run(_interactive_async(agent, model, provider))

if __name__ == "__main__":
    app()
```

---

## 14. 项目文件结构

```
python-opencode/
├── pyproject.toml                    # 项目配置 (uv/poetry)
├── README.md
├── opencode/
│   ├── __init__.py
│   ├── cli.py                        # CLI 入口 (Typer)
│   │
│   ├── models/                       # SQLAlchemy 数据模型
│   │   ├── __init__.py               # Base + engine 工厂
│   │   ├── session.py                # Session 表
│   │   ├── message.py                # Message 表 (事件溯源)
│   │   └── agent_config.py           # AgentConfig 表
│   │
│   ├── agent/                        # Agent 系统
│   │   ├── __init__.py
│   │   ├── agent.py                  # AgentInfo 数据类
│   │   ├── builtins.py               # 7 种内置 Agent
│   │   ├── registry.py               # AgentRegistry
│   │   └── permissions.py            # PermissionRuleset
│   │
│   ├── session/                      # Session 管理
│   │   ├── __init__.py
│   │   ├── session.py                # SessionService (CRUD)
│   │   ├── runner.py                 # AgentLoop (核心循环)
│   │   ├── coordinator.py            # RunCoordinator (FIFO)
│   │   ├── compaction.py             # CompactionService
│   │   ├── system.py                 # SystemPromptEngine
│   │   ├── message_convert.py        # to_model_messages()
│   │   └── overflow.py              # Token overflow 检测
│   │
│   ├── tool/                         # 工具系统
│   │   ├── __init__.py
│   │   ├── tool.py                   # Tool 基类 + ToolContext + ToolResult
│   │   ├── registry.py               # ToolRegistry
│   │   └── builtins/
│   │       ├── __init__.py           # 注册所有内置工具
│   │       ├── read.py
│   │       ├── write.py
│   │       ├── edit.py
│   │       ├── bash.py
│   │       ├── glob.py
│   │       ├── grep.py
│   │       ├── task.py               # TaskTool (子代理)
│   │       ├── webfetch.py
│   │       ├── websearch.py
│   │       ├── question.py
│   │       ├── skill_tool.py
│   │       ├── todowrite.py
│   │       ├── apply_patch.py
│   │       ├── lsp.py
│   │       └── plan_exit.py
│   │
│   ├── provider/                     # LLM Provider 层
│   │   ├── __init__.py
│   │   ├── base.py                   # ProviderClient 接口 + StreamEvent
│   │   ├── anthropic_client.py       # Anthropic SDK 封装
│   │   ├── openai_client.py          # OpenAI SDK 封装
│   │   ├── factory.py                # ProviderFactory
│   │   └── catalog.py                # ModelCatalog
│   │
│   ├── mcp/                          # MCP 集成
│   │   ├── __init__.py
│   │   ├── client.py                 # MCPClient
│   │   └── catalog.py                # MCP 工具 → Tool Registry 转换
│   │
│   ├── context/                      # Context Engine
│   │   ├── __init__.py
│   │   └── token.py                  # Token 估算
│   │
│   ├── skill/                        # Skill 系统
│   │   ├── __init__.py
│   │   └── skill.py                  # SkillLoader + Skill
│   │
│   ├── background/                   # Background Job
│   │   ├── __init__.py
│   │   └── job.py                    # BackgroundJobManager
│   │
│   └── config/                       # 配置管理
│       ├── __init__.py
│       └── config.py                 # Pydantic Settings
│
├── prompts/                          # Prompt 模板
│   ├── anthropic.txt
│   ├── beast.txt
│   ├── codex.txt
│   ├── gpt.txt
│   ├── gemini.txt
│   ├── trinity.txt
│   ├── default.txt
│   └── generate_agent.txt
│
└── tests/                            # 测试
    ├── __init__.py
    ├── conftest.py
    ├── test_session.py
    ├── test_agent.py
    ├── test_tools/
    │   ├── test_read.py
    │   ├── test_write.py
    │   ├── test_edit.py
    │   └── test_task.py
    ├── test_runner.py
    ├── test_compaction.py
    └── test_provider/
        ├── test_anthropic.py
        └── test_openai.py
```

---

## 15. 依赖 (pyproject.toml)

```toml
[project]
name = "python-opencode"
version = "0.1.0"
description = "Python port of the OpenCode Agent Framework"
requires-python = ">=3.11"

dependencies = [
    # Async
    "aiosqlite>=0.20.0",

    # SQLAlchemy 2.0 async
    "sqlalchemy[asyncio]>=2.0.0",

    # LLM SDKs
    "openai>=1.0.0",
    "anthropic>=0.30.0",

    # CLI
    "typer>=0.12.0",
    "rich>=13.0.0",

    # Prompt templates
    "jinja2>=3.1.0",

    # MCP
    "mcp>=1.0.0",

    # Skill frontmatter parsing
    "python-frontmatter>=1.0.0",

    # Unique IDs
    "python-ulid>=2.0.0",

    # Config
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "pyyaml>=6.0",

    # Logging
    "structlog>=24.0.0",

    # HTTP (websearch/webfetch)
    "httpx>=0.27.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-mock>=3.12.0",
    "ruff>=0.5.0",
    "mypy>=1.10.0",
]
```

---

## 16. 实施计划

### Phase 1 — 核心骨架（2-3 周）

**目标**: 单 Agent 能跑通一个完整的 "用户输入 → LLM 回答" 循环

- [ ] 数据模型: `Session`, `Message` 表 + SQLAlchemy async engine
- [ ] `SessionService`: create/fork/remove/get_messages
- [ ] `AgentInfo` + `AgentRegistry` + 7 个内置 Agent
- [ ] `Tool` 基类 + `ToolRegistry`
- [ ] 5 个基本工具: read, write, edit, bash, glob
- [ ] `AnthropicClient` + `OpenAIClient` (stream + generate_object)
- [ ] `ModelCatalog` 静态注册
- [ ] `SystemPromptEngine` + 1 个模板 (anthropic.txt)
- [ ] `AgentLoop._run_turn` — 单轮 LLM + 工具执行
- [ ] CLI: `opencode run "prompt"`

**验证**: `opencode run "用 python 写一个 hello world 并运行它"` 能完成

### Phase 2 — 完整能力（2 周）

**目标**: 完整复刻 OpenCode 的功能集

- [ ] 完整的 `AgentLoop._run_loop` — 多轮 + 退出条件 + 步数限制
- [ ] `RunCoordinator` — 并发控制
- [ ] `CompactionService` — 上下文压缩
- [ ] `BackgroundJobManager` — 后台任务
- [ ] `TaskTool` — 子代理创建
- [ ] 剩余 10 个内置工具
- [ ] `MCPClient` — MCP 集成
- [ ] `SkillLoader` — Skill 系统
- [ ] 多模型 Prompt 模板
- [ ] `PermissionRuleset` 完整实现
- [ ] CLI: interactive REPL

### Phase 3 — 多 Agent 编排（2-3 周）

**目标**: 实现架构分析文档第四阶段的扩展方案

- [ ] `ExecutiveAgent` + Planner Prompt + `TaskGraph` JSON 生成
- [ ] `TaskGraphEngine` — DAG 数据结构 + 拓扑排序 + 层级并发
- [ ] `CapabilityRouter` — 评分公式 + 模型选择
- [ ] Worker Pool 类型拆分: code, test, document, search
- [ ] Worker Context 精简构建器
- [ ] `ReflectionAgent` — 失败 Critic 触发

### Phase 4 — 生产化（持续）

- [ ] FastAPI REST API
- [ ] WebSocket 实时流输出
- [ ] 持久运行模式（7×24 Session 事件循环）
- [ ] 成本追踪
- [ ] 向量检索集成 (Chroma/Qdrant)
- [ ] 消息平台适配 (Telegram/Slack)
- [ ] Docker 部署
- [ ] 性能测试 + 优化

---

## 17. 关键设计决策

### 为什么不用 LangChain/LlamaIndex

| | 自研 | LangChain |
|---|---|---|
| Agent Loop 透明度 | 完全可控 | 黑盒抽象，调试困难 |
| Prompt 控制 | Jinja2 模板，模型级定制 | 通用模板，定制受限 |
| 工具执行 | 简单协程，无魔法 | 复杂 Chain/AgentExecutor |
| 依赖 | asyncio + SDK | 重量级依赖树 |
| 学习曲线 | 阅读文档即可 | 需要学习框架概念 |

### Effect-TS Structured Concurrency → asyncio.TaskGroup

Effect-TS 的 Fiber/Scope 提供结构化并发，`asyncio.TaskGroup`（Python 3.11+）是标准库等价物：

```python
# Effect-TS: Effect.all(tasks).pipe(Effect.withConcurrency(4))
async with asyncio.TaskGroup() as tg:
    tasks = [tg.create_task(coro) for coro in coros]
# 自动等待所有 task 完成

# 如果想要并发限制，用 asyncio.Semaphore
```

### 事件溯源 Message 存储

选择 JSON 列存储 parts 而非每行一个 event，理由：
- SQLite 写入更少，性能更好
- 单条消息的 parts 通常不多（< 20）
- 查询简化：`SELECT parts FROM messages WHERE session_id=?`

如果需要事件溯源的高级能力（重放、审计），可以加一张 `message_events` 表。

### 工具并发 vs 串行

参照 OpenCode 的 FiberSet 设计，工具调用**默认并行执行**。如果工具间有依赖，由 Agent（LLM）通过多次 tool_call 分轮次处理。

### 上下文压缩策略

默认使用 Head/Tail 分割 + LLM 摘要（V2 风格），不实现 V1 的 prune 机制（擦除旧工具输出）。如果后续需要，可追加。
