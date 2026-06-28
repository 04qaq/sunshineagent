"""CLI 入口 —— SunshineAgent 命令行工具。

OWNER: Human
SKILL: Typer, Rich

使用方式：
    sunshine                   → 交互 REPL
    sunshine run "prompt"      → 单次执行
    sunshine serve             → API 服务 (Phase 4)
"""

import asyncio
import contextlib
import signal
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.agent.builtins import AgentRegistry
from src.agent.loop import AgentLoop, SessionContext
from src.agent.permissions import PermissionRuleset
from src.background import BackgroundJobManager
from src.cli.permission_ui import PermissionUI, QuestionUI
from src.cli.prompt import PromptHistory, PromptInput, PromptParser
from src.cli.queue import PromptQueue
from src.cli.status_bar import StatusBar
from src.config.config import get_config, load_config, save_config
from src.mcp import (
    MCPClient,
    MCPServerConfig,
    load_all_configs,
    load_project_configs,
    remove_mcp_config,
    save_mcp_config,
)
from src.models.database import Database
from src.prompt.engine import SystemPromptEngine
from src.provider.factory import ProviderFactory
from src.provider.registry import ProviderRegistry
from src.provider.router import ModelRouter
from src.session.compaction import CompactionService
from src.session.coordinator import RunCoordinator
from src.session.service import SessionService
from src.skill import SkillLoader
from src.tool.apply_patch import ApplyPatchTool
from src.tool.base import ToolRegistry
from src.tool.bash import BashTool
from src.tool.edit import EditTool
from src.tool.glob import GlobTool
from src.tool.grep import GrepTool
from src.tool.lsp import LSPTool
from src.tool.plan_exit import PlanExitTool
from src.tool.question import QuestionTool
from src.tool.read import ReadTool
from src.tool.skill_tool import SkillTool
from src.tool.task import TaskTool
from src.tool.todowrite import TodoWriteTool
from src.tool.webfetch import WebFetchTool
from src.tool.websearch import WebSearchTool
from src.tool.write import WriteTool

app = typer.Typer(
    name="sunshine",
    help="SunshineAgent — AI coding agent",
    invoke_without_command=True,
)
console = Console()


class AppContext:
    """REPL 运行时的全局状态。"""

    def __init__(self):
        self.config = get_config()
        self.db: Database | None = None
        self.sessions: SessionService | None = None
        self.agents: AgentRegistry | None = None
        self.tools: ToolRegistry | None = None
        self.provider_factory: ProviderFactory | None = None
        self.registry: ProviderRegistry | None = None
        self.coordinator: RunCoordinator = RunCoordinator()
        self.jobs: BackgroundJobManager = BackgroundJobManager()
        self.system_engine: SystemPromptEngine | None = None
        self.compaction: CompactionService | None = None
        self.mcp: MCPClient | None = None
        self._current_session_id: str | None = None
        self._loop_factory = None
        self._mcp_configs: list[MCPServerConfig] = []

        # 新增：交互组件
        self.history: PromptHistory = PromptHistory()
        self.status_bar: StatusBar = StatusBar()
        self.permission_ui: PermissionUI = PermissionUI(console)
        self.question_ui: QuestionUI = QuestionUI(console)
        self.queue: PromptQueue | None = None

    @property
    def current_session_id(self) -> str | None:
        return self._current_session_id

    def make_loop(self) -> AgentLoop:
        if self._loop_factory:
            return self._loop_factory()
        raise RuntimeError("loop_factory not set")


async def _init(ctx: AppContext, workspace: Path):
    """初始化数据库、服务、工具、MCP。"""
    c = ctx.config
    c.workspace_root = str(workspace.resolve())

    db_path = str(workspace / ".sunshine" / "state.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    ctx.db = Database(db_path=db_path)
    await ctx.db.init()

    ctx.sessions = SessionService(ctx.db)
    ctx.agents = AgentRegistry(ctx.db._session_factory)
    ctx.tools = ToolRegistry()
    ctx.registry = ProviderRegistry(c.workspace_root)
    ctx.provider_factory = ProviderFactory(ctx.registry)

    skill_dirs = [
        str(Path.home() / ".opencode" / "skills"),
        str(workspace / ".opencode" / "skills"),
    ]
    skill_loader = SkillLoader(skill_dirs)
    await skill_loader.load_all()

    ctx.system_engine = SystemPromptEngine(c.prompts_dir, skill_loader=skill_loader)
    ctx.compaction = CompactionService(ctx.provider_factory, ctx.sessions)
    ctx.mcp = MCPClient()

    _register_tools(ctx, workspace, skill_loader)

    # 连接项目 MCP servers（自动加载，Agent 可见）
    project_mcp = load_project_configs(c.workspace_root)
    for mcfg in project_mcp:
        try:
            mcp_tools = await ctx.mcp.connect(mcfg)
            for mt in mcp_tools:
                ctx.tools.register(mt)
            ctx._mcp_configs.append(mcfg)
            console.print(f"[dim]MCP: {mcfg.name} — {len(mcp_tools)} tools[/dim]")
        except Exception as e:
            console.print(f"[yellow]MCP {mcfg.name}: {e}[/yellow]")

    # 加载全局 MCP 配置（仅存储，不自动连接）
    global_mcp = load_all_configs(c.workspace_root)
    for mcfg in global_mcp:
        if mcfg.source == "global" and mcfg.name not in {c.name for c in ctx._mcp_configs}:
            ctx._mcp_configs.append(mcfg)


def _register_tools(ctx: AppContext, workspace: Path, skill_loader=None):
    ws = str(workspace)
    t = ctx.tools
    t.register(ReadTool(ws))
    t.register(WriteTool(ws))
    t.register(EditTool(ws))
    t.register(BashTool(ws))
    t.register(GlobTool(ws))
    t.register(GrepTool(ws))
    t.register(WebFetchTool())
    t.register(WebSearchTool())
    t.register(QuestionTool())
    t.register(TodoWriteTool())
    t.register(ApplyPatchTool(ws))
    t.register(LSPTool())
    t.register(PlanExitTool())
    t.register(SkillTool(skill_loader))

    def _lf():
        return AgentLoop(
            session_service=ctx.sessions,
            agent_registry=ctx.agents,
            tool_registry=ctx.tools,
            provider_factory=ctx.provider_factory,
            compaction_service=ctx.compaction,
            coordinator=ctx.coordinator,
            system_prompt_engine=ctx.system_engine,
        )

    ctx._loop_factory = _lf
    router = ModelRouter()
    t.register(TaskTool(ctx.sessions, ctx.agents, _lf, ctx.jobs, router, ctx.registry))


async def _send_prompt(
    ctx: AppContext,
    prompt: str,
    *,
    agent_name: str | None = None,
    model_id: str | None = None,
    provider_id: str | None = None,
    steps: int | None = None,
    quiet: bool = False,
    abort: asyncio.Event | None = None,
) -> str | None:
    c = ctx.config
    reg = ctx.registry
    _agent = agent_name or c.default_agent
    _model = model_id or (reg.default_model if reg else c.default_model)
    _provider = provider_id or (reg.default_provider if reg else c.default_provider)

    # 解析模型：如果是 "provider/model" 格式，拆出纯 model 名
    raw_model = _model
    if "/" in raw_model:
        _provider = raw_model.split("/")[0]
        raw_model = raw_model.split("/", 1)[1]

    if ctx._current_session_id is None:
        session = await ctx.sessions.create(
            agent=_agent, provider_id=_provider, model_id=_model,
        )
        ctx._current_session_id = session.id

    # 解析提示中的引用（用于后续处理文件附件等）
    PromptParser.parse(prompt, c.workspace_root)

    await ctx.sessions.create_message(
        ctx._current_session_id, "user", parts=[{"type": "text", "text": prompt}]
    )

    # 更新状态栏
    ctx.status_bar.agent = _agent
    ctx.status_bar.model = raw_model
    ctx.status_bar.provider = _provider
    ctx.status_bar.session_id = ctx._current_session_id or ""
    ctx.status_bar.start()

    def _on_text(text: str):
        if not quiet:
            console.print(text, end="")

    abort_sig = abort or asyncio.Event()

    sctx = SessionContext(
        session_id=ctx._current_session_id,
        agent_name=_agent,
        provider_id=_provider,
        model_id=raw_model,
        max_steps=steps,
        permission=PermissionRuleset.all(),
        workspace=c.workspace_root,
        on_text_delta=_on_text,
        abort_signal=abort_sig,
    )

    if not quiet:
        console.print()

    loop = ctx.make_loop()
    result_msg_id = await loop.run(sctx)

    # 停止状态栏
    ctx.status_bar.stop()

    if not quiet:
        console.print()
        # 显示状态栏
        console.print(f"[dim]{ctx.status_bar.render()}[/dim]")

    return result_msg_id


def _has_key(c, provider: str, registry=None) -> bool:
    if registry:
        p = registry.get_provider(provider)
        if p:
            return bool(p.api_key)
    return False


def _key_status(c, provider: str, registry=None) -> str:
    return "[green]✓[/green]" if _has_key(c, provider, registry) else "[red]✗ 未设置[/red]"


def _get_base_url(c, provider: str, registry=None) -> str | None:
    if registry:
        p = registry.get_provider(provider)
        if p and p.base_url:
            return p.base_url
    return None


# ── run 命令 ────────────────────────────────────────────────────────


@app.command()
def run(
    prompt: str = typer.Argument(..., help="输入给 Agent 的 prompt"),
    agent: str = typer.Option("build", help="使用的 Agent 名称"),
    model: str = typer.Option("claude-sonnet-4-6", help="模型 ID"),
    provider: str = typer.Option("anthropic", help="Provider ID (anthropic / openai)"),
    base_url: str = typer.Option(None, help="API base URL"),
    steps: int = typer.Option(None, help="最大步数限制"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="静默模式"),
    workspace: Path = typer.Option(  # noqa: B008
        Path.cwd(), help="工作区目录", exists=True, file_okay=False  # noqa: B008
    ),
):
    """单次 prompt，运行完退出"""

    async def _go():
        ctx = AppContext()
        await _init(ctx, workspace)
        load_config(ctx.config)
        if base_url:
            p = ctx.registry.get_provider(provider)
            if p:
                p.base_url = base_url
            else:
                ctx.registry.add_provider(provider, provider.title(), base_url=base_url)
        await _send_prompt(
            ctx, prompt, agent_name=agent, model_id=model,
            provider_id=provider, steps=steps, quiet=quiet,
        )
        await ctx.db.close()

    asyncio.run(_go())


# ── serve 命令 ───────────────────────────────────────────────────────


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="监听地址"),
    port: int = typer.Option(4096, help="监听端口"),
):
    """启动 HTTP API 服务（Phase 4）"""
    console.print(f"[yellow]serve 命令将在 Phase 4 实现[/yellow] (http://{host}:{port})")


# ── 默认命令：交互 REPL ──────────────────────────────────────────────


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    agent: str = typer.Option("build", help="使用的 Agent 名称"),
    model: str = typer.Option("claude-sonnet-4-6", help="模型 ID"),
    provider: str = typer.Option("anthropic", help="Provider ID (anthropic / openai)"),
    base_url: str = typer.Option(None, help="API base URL"),
    steps: int = typer.Option(None, help="最大步数限制"),
    workspace: Path = typer.Option(  # noqa: B008
        Path.cwd(), help="工作区目录", exists=True, file_okay=False  # noqa: B008
    ),
):
    """SunshineAgent — AI coding agent。无子命令时进入交互 REPL。"""
    if ctx.invoked_subcommand is not None:
        return

    asyncio.run(_repl_async(agent, model, provider, base_url, steps, workspace))


async def _repl_async(
    agent_name: str,
    model_id: str,
    provider_id: str,
    base_url: str | None,
    steps: int | None,
    workspace: Path,
):
    app_ctx = AppContext()
    c = app_ctx.config

    await _init(app_ctx, workspace)
    load_config(c)

    if base_url:
        p = app_ctx.registry.get_provider(provider_id)
        if p:
            p.base_url = base_url
        else:
            app_ctx.registry.add_provider(provider_id, provider_id.title(), base_url=base_url)

    reg = app_ctx.registry
    _agent = c.default_agent
    _model = reg.default_model
    _provider = reg.default_provider

    # 设置已知 Agent 名称
    known_agents = {a.name for a in app_ctx.agents.list(include_hidden=True)}
    PromptParser.set_known_agents(known_agents)

    console.clear()
    console.print(
        Panel.fit(
            "[bold cyan]SunshineAgent[/bold cyan]\n"
            "[dim]输入 prompt 开始对话  [/dim]"
            "[bold]/help[/bold] 帮助  [bold]/exit[/bold] 退出  "
            "[bold]Ctrl+C[/bold] 中断  [bold]↑↓[/bold] 历史",
            title="Sunshine",
            border_style="cyan",
        )
    )

    abort = asyncio.Event()

    def _on_sigint():
        if not abort.is_set():
            abort.set()
            app_ctx.status_bar.increment_interrupt()
            console.print("\n[dim]中断信号已发送，等待 agent 停止...[/dim]")

    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGINT, lambda s, f: _on_sigint())

    # 初始化 Prompt Queue
    async def _run_prompt(prompt_input: PromptInput, abort_signal: asyncio.Event):
        await _send_prompt(
            app_ctx, prompt_input.text,
            agent_name=_agent, model_id=_model, provider_id=_provider,
            steps=steps, abort=abort_signal,
        )

    async def _new_session():
        session = await app_ctx.sessions.create(
            agent=c.default_agent,
            provider_id=c.default_provider,
            model_id=c.default_model,
        )
        app_ctx._current_session_id = session.id
        console.print(f"[dim]新会话: {session.id}[/dim]")

    app_ctx.queue = PromptQueue(
        run_fn=_run_prompt,
        on_new_session=_new_session,
        on_status=lambda s: setattr(app_ctx.status_bar, 'phase', s),
    )

    while True:
        try:
            abort.clear()
            app_ctx.status_bar.reset_interrupt()

            # 构建提示文本
            if app_ctx._current_session_id:
                session_short = app_ctx._current_session_id[:8]
                prompt_text = f"[dim][{session_short}][/dim] [bold green]> [/bold green]"
            else:
                prompt_text = "[bold green]> [/bold green]"

            # 显示状态栏
            status_line = app_ctx.status_bar.render(compact=True)
            if status_line:
                console.print(f"[dim]{status_line}[/dim]")

            # 获取用户输入（支持历史导航）
            try:
                from prompt_toolkit import PromptSession
                from prompt_toolkit.history import InMemoryHistory

                # 使用 prompt_toolkit 获取输入（支持历史导航）
                session = PromptSession(history=InMemoryHistory())
                user_input = await session.prompt_async(prompt_text)
            except ImportError:
                # 降级到简单输入
                user_input = console.input(prompt_text)

        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见[/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # 添加到历史记录
        app_ctx.history.push(user_input)

        if user_input.startswith("/"):
            handled = await _handle_command(
                app_ctx, user_input, _agent, _model, _provider, steps
            )
            if handled == "exit":
                break
            if handled == "reload":
                _agent = c.default_agent
                _model = c.default_model
                _provider = c.default_provider
            continue

        # 提交到队列
        prompt_input = PromptParser.parse(user_input, c.workspace_root)
        app_ctx.queue.submit(prompt_input)

    await app_ctx.db.close()


# ── REPL 命令处理 ───────────────────────────────────────────────────


async def _handle_command(
    ctx: AppContext,
    cmd: str,
    agent_name: str,
    model_id: str,
    provider_id: str,
    steps: int | None,
) -> str | None:
    parts = cmd.split(maxsplit=1)
    action = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    c = ctx.config

    if action in ("/exit", "/quit", "/q"):
        console.print("[dim]再见[/dim]")
        return "exit"

    if action == "/help":
        _print_help()
        return None

    if action == "/status":
        _print_status(ctx)
        return None

    if action == "/apikey":
        if not args:
            console.print("[red]用法: /apikey <provider> <key>[/red]")
            return None
        parts2 = args.split(maxsplit=1)
        prov = parts2[0].lower()
        key = parts2[1] if len(parts2) > 1 else ""
        if not ctx.registry.get_provider(prov):
            console.print(f"[red]未知 provider: {prov}[/red]")
            return None
        ctx.registry.set_key(prov, key)
        ctx.registry.save()
        ctx.provider_factory._clients.clear()
        console.print(f"[green]✓ {prov} API key 已设置[/green]")
        return None

    if action == "/baseurl":
        if not args:
            console.print("[red]用法: /baseurl <provider> <url>[/red]")
            return None
        parts2 = args.split(maxsplit=1)
        prov = parts2[0].lower()
        url = parts2[1] if len(parts2) > 1 else ""
        if not ctx.registry.get_provider(prov):
            console.print(f"[red]未知 provider: {prov}[/red]")
            return None
        ctx.registry.set_url(prov, url)
        ctx.registry.save()
        ctx.provider_factory._clients.clear()
        console.print(f"[green]✓ {prov} base_url = {url or '默认'}[/green]")
        return None

    if action == "/model":
        if not args:
            _model_list(ctx)
            return None
        m = ctx.registry.resolve(args)
        if not m:
            console.print(f"[red]模型未找到: {args}[/red]")
            return None
        reg = ctx.registry
        reg.default_model = m.full_id
        reg.default_provider = m.provider
        reg.save()
        console.print(f"[green]✓ {m.name} ({m.full_id})[/green]")
        return "reload"

    if action == "/models":
        _model_list(ctx)
        return None

    if action == "/providers":
        _provider_list(ctx)
        return None

    if action == "/provider":
        if not args:
            console.print("[red]用法: /provider <openai|anthropic>[/red]")
            return None
        if args not in ("openai", "anthropic"):
            console.print(f"[red]未知 provider: {args}[/red]")
            return None
        c.default_provider = args
        console.print(f"[green]✓ provider = {args}[/green]")
        save_config(c)
        return "reload"

    if action == "/agent":
        if not args:
            agents = ctx.agents.list(include_hidden=False)
            for a in agents:
                console.print(f"  [bold]{a.name}[/bold] — {a.mode}")
            return None
        c.default_agent = args
        console.print(f"[green]✓ agent = {args}[/green]")
        save_config(c)
        return "reload"

    if action in ("/clear", "/new"):
        session = await ctx.sessions.create(
            agent=c.default_agent,
            provider_id=c.default_provider,
            model_id=c.default_model,
        )
        ctx._current_session_id = session.id
        console.print(f"[dim]新会话: {session.id}[/dim]")
        return None

    if action == "/sessions":
        from sqlalchemy import select

        from src.models.session import Session as SessModel
        async with ctx.db.session() as db:
            result = await db.execute(
                select(SessModel).order_by(SessModel.created_at.desc()).limit(20)
            )
            sessions = list(result.scalars())
        table = Table(title="最近会话")
        table.add_column("ID", style="dim")
        table.add_column("Agent")
        table.add_column("Model")
        table.add_column("Status")
        table.add_column("Title")
        for s in sessions:
            marker = "→" if s.id == ctx._current_session_id else " "
            table.add_row(
                f"{marker} {s.id[:20]}...",
                s.agent or "-",
                s.model_id or "-",
                s.status or "-",
                (s.title or "-")[:40],
            )
        console.print(table)
        console.print("[dim]/resume <id> 恢复会话[/dim]")
        return None

    if action == "/resume":
        if not args:
            console.print("[red]用法: /resume <session-id>[/red]")
            return None
        ctx._current_session_id = args
        console.print(f"[green]✓ 已恢复到会话 {args}[/green]")
        return None

    if action == "/mcp":
        if not args or args == "list":
            _mcp_list(ctx)
            return None
        parts2 = args.split(maxsplit=1)
        sub = parts2[0].lower()
        rest = parts2[1] if len(parts2) > 1 else ""
        if sub == "enable":
            await _mcp_enable(ctx, rest)
        elif sub == "disable":
            await _mcp_disable(ctx, rest)
        elif sub == "add":
            global_ = False
            add_args = rest
            if rest.startswith("--global "):
                global_ = True
                add_args = rest[len("--global "):]
            elif rest == "--global":
                console.print("[red]用法: /mcp add --global <name> <command> [args...][/red]")
                return None
            await _mcp_add(ctx, add_args, global_=global_)
        elif sub == "remove":
            await _mcp_remove(ctx, rest)
        else:
            _mcp_list(ctx)
        return None

    console.print(f"[red]未知命令: {action}[/red] 输入 /help 查看帮助")
    return None


# ── MCP 管理 ─────────────────────────────────────────────────────────


def _mcp_list(ctx: AppContext):
    """列出所有 MCP server：启用的（已连接）+ 待启用的。"""
    active = {t.get("server", "") for t in ctx.mcp.list_tools()}
    if not ctx._mcp_configs:
        console.print("[dim]没有 MCP server 配置[/dim]")
        console.print("拖 .json 文件到 .sunshine/mcp/ 目录，或用 [bold]/mcp add[/bold]")
        return

    table = Table(title="MCP Servers")
    table.add_column("状态")
    table.add_column("名称")
    table.add_column("来源")
    table.add_column("命令")
    for cfg in ctx._mcp_configs:
        status = "[green]✓ 启用[/green]" if cfg.name in active else "[dim]○ 待启用[/dim]"
        src = "全局" if cfg.source == "global" else "项目"
        table.add_row(status, cfg.name, src, f"{cfg.command} {' '.join(cfg.args)}")
    console.print(table)
    if any(cfg.name not in active for cfg in ctx._mcp_configs):
        console.print("[dim]/mcp enable <name> 启用  /mcp disable <name> 禁用[/dim]")


async def _mcp_enable(ctx: AppContext, name: str):
    """启用全局 MCP 到当前项目。"""
    cfg = next((c for c in ctx._mcp_configs if c.name == name and c.name not in
                {t.get("server", "") for t in ctx.mcp.list_tools()}), None)
    if not cfg:
        console.print(f"[red]{name} 未找到或已启用[/red]")
        return
    try:
        tools = await ctx.mcp.connect(cfg)
        for mt in tools:
            ctx.tools.register(mt)
        console.print(f"[green]✓ {name} — {len(tools)} tools[/green]")
    except Exception as e:
        console.print(f"[red]启用失败: {e}[/red]")


async def _mcp_disable(ctx: AppContext, name: str):
    """禁用 MCP（不断开连接在 init 中的配置，只移除已连接的）。"""
    await ctx.mcp.disconnect(name)
    console.print(f"[green]✓ {name} 已禁用[/green]")


async def _mcp_add(ctx: AppContext, args: str, global_: bool = False):
    """添加 MCP server 配置并自动连接。"""
    if not args:
        console.print("[red]用法: /mcp add <name> <command> [arg1 arg2 ...][/red]")
        return
    parts = args.split()
    if len(parts) < 2:
        console.print("[red]至少需要 name 和 command[/red]")
        return
    name = parts[0]
    command = parts[1]
    cmd_args = parts[2:] if len(parts) > 2 else []
    source = "global" if global_ else "project"
    workspace = ctx.config.workspace_root if not global_ else ""
    config = MCPServerConfig(name=name, command=command, args=cmd_args, source=source)
    try:
        tools = await ctx.mcp.connect(config)
        for mt in tools:
            ctx.tools.register(mt)
        # 持久化
        if global_:
            config.source = "global"
        else:
            config.env = {"_workspace": workspace}
        save_mcp_config(config)
        ctx._mcp_configs.append(config)
        console.print(f"[green]✓ MCP {name} ({source}) — {len(tools)} tools[/green]")
    except Exception as e:
        console.print(f"[red]连接失败: {e}[/red]")


async def _mcp_remove(ctx: AppContext, name: str):
    """删除 MCP 配置并断开连接。"""
    if not name:
        console.print("[red]用法: /mcp remove <name>[/red]")
        return
    await ctx.mcp.disconnect(name)
    ctx._mcp_configs = [c for c in ctx._mcp_configs if c.name != name]
    remove_mcp_config(name, ctx.config.workspace_root)
    console.print(f"[green]✓ MCP server {name} 已删除[/green]")


def _model_list(ctx):
    """列出所有可用模型，按 provider 分组。"""
    reg = ctx.registry
    table = Table(title="可用模型")
    table.add_column("模型 ID", style="dim")
    table.add_column("名称")
    table.add_column("Cost")
    table.add_column("Cap")
    for pid in reg.providers:
        models = reg.list_models(pid)
        for m in models:
            cur = "→" if m.full_id == reg.default_model else " "
            table.add_row(f"{cur} {m.full_id}", m.name, m.cost, m.capability)
    console.print(table)
    console.print(f"[dim]当前: {reg.default_model}  /model <id> 切换[/dim]")


def _provider_list(ctx):
    """列出所有已配置的 provider。"""
    reg = ctx.registry
    table = Table(title="Providers")
    table.add_column("ID")
    table.add_column("名称")
    table.add_column("Key")
    table.add_column("URL")
    table.add_column("模型数")
    for pid, p in reg.providers.items():
        key_icon = "✓" if p.api_key else "✗"
        table.add_row(pid, p.name, key_icon, p.base_url or "(默认)", str(len(p.models)))
    console.print(table)


# ── 帮助 & 状态 ──────────────────────────────────────────────────────


def _print_help():
    console.print(
        Panel.fit(
            "[bold]REPL 命令[/bold]\n\n"
            "[bold]/help[/bold]               显示帮助\n"
            "[bold]/exit[/bold], /quit, /q    退出\n"
            "[bold]/status[/bold]             显示当前配置\n\n"
            "[bold]/apikey <prov> <key>[/bold] 设置 API key\n"
            "[bold]/baseurl <url>[/bold]       设置代理地址\n"
            "[bold]/model <id>[/bold]          切换模型\n"
            "[bold]/provider <id>[/bold]       切换 provider\n"
            "[bold]/agent <name>[/bold]        切换 agent\n\n"
            "[bold]/clear[/bold], /new         开始新会话\n"
            "[bold]/sessions[/bold]            列出最近会话\n"
            "[bold]/resume <id>[/bold]         恢复指定会话\n\n"
            "[bold]/mcp[/bold]                 列出所有 MCP server\n"
            "[bold]/mcp enable <name>[/bold]   启用全局 MCP 到项目\n"
            "[bold]/mcp disable <name>[/bold]  禁用 MCP（保留配置）\n"
            "[bold]/mcp add <n> <cmd> [a][/bold] 添加项目 MCP\n"
            "[bold]/mcp add --global <n> <cmd> [a][/bold] 添加全局 MCP\n"
            "[bold]/mcp remove <name>[/bold]   删除 MCP\n\n"
            "[bold]交互功能[/bold]\n"
            "[bold]↑↓[/bold]                  浏览历史记录\n"
            "[bold]@filename[/bold]           引用文件\n"
            "[bold]@agent[/bold]              切换 Agent\n"
            "[bold]Ctrl+C[/bold]              中断当前执行\n\n"
            "直接输入内容则发送给 Agent",
            title="帮助",
            border_style="blue",
        )
    )


def _print_status(ctx):
    reg = ctx.registry
    c = ctx.config
    p = reg.get_provider(reg.default_provider)
    key_status = _key_status(c, reg.default_provider, reg)
    base_url = p.base_url if p else ""
    console.print(
        Panel.fit(
            f"provider : [bold]{reg.default_provider}[/bold]\n"
            f"model    : [bold]{reg.default_model}[/bold]\n"
            f"api_key  : {key_status}\n"
            f"base_url : {base_url or '默认'}\n"
            f"workspace: {c.workspace_root}",
            title="状态",
            border_style="green",
        )
    )


if __name__ == "__main__":
    app()
