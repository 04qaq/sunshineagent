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
from src.provider.catalog import ModelCatalog
from src.provider.factory import ProviderFactory
from src.session.compaction import CompactionService
from src.session.coordinator import RunCoordinator
from src.session.service import SessionService
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
        self.catalog: ModelCatalog | None = None
        self.coordinator: RunCoordinator = RunCoordinator()
        self.jobs: BackgroundJobManager = BackgroundJobManager()
        self.system_engine: SystemPromptEngine | None = None
        self.compaction: CompactionService | None = None
        self.mcp: MCPClient | None = None
        self._current_session_id: str | None = None
        self._loop_factory = None
        self._mcp_configs: list[MCPServerConfig] = []

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
    ctx.catalog = ModelCatalog(c.workspace_root)
    ctx.provider_factory = ProviderFactory(ctx.catalog)
    ctx.system_engine = SystemPromptEngine(c.prompts_dir)
    ctx.compaction = CompactionService(ctx.provider_factory, ctx.sessions)
    ctx.mcp = MCPClient()

    _register_tools(ctx, workspace)

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


def _register_tools(ctx: AppContext, workspace: Path):
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
    t.register(SkillTool(None))

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
    t.register(TaskTool(ctx.sessions, ctx.agents, _lf, ctx.jobs))


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
    _agent = agent_name or c.default_agent
    _model = model_id or c.default_model
    _provider = provider_id or c.default_provider

    if ctx._current_session_id is None:
        session = await ctx.sessions.create(
            agent=_agent, provider_id=_provider, model_id=_model,
        )
        ctx._current_session_id = session.id

    await ctx.sessions.create_message(
        ctx._current_session_id, "user", parts=[{"type": "text", "text": prompt}]
    )

    def _on_text(text: str):
        if not quiet:
            console.print(text, end="")

    abort_sig = abort or asyncio.Event()

    sctx = SessionContext(
        session_id=ctx._current_session_id,
        agent_name=_agent,
        provider_id=_provider,
        model_id=_model,
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
    if not quiet:
        console.print()
    return result_msg_id


def _has_key(c, provider: str) -> bool:
    if provider == "openai":
        return bool(c.openai_api_key)
    if provider == "anthropic":
        return bool(c.anthropic_api_key)
    return False


def _key_status(c, provider: str) -> str:
    return "[green]✓[/green]" if _has_key(c, provider) else "[red]✗ 未设置[/red]"


def _get_base_url(c, provider: str) -> str | None:
    if provider == "openai":
        return c.openai_base_url
    if provider == "anthropic":
        return c.anthropic_base_url
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
        if base_url:
            if provider == "openai":
                ctx.config.openai_base_url = base_url
            elif provider == "anthropic":
                ctx.config.anthropic_base_url = base_url
        await _init(ctx, workspace)
        load_config(ctx.config)
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

    if base_url:
        if provider_id == "openai":
            c.openai_base_url = base_url
        elif provider_id == "anthropic":
            c.anthropic_base_url = base_url

    await _init(app_ctx, workspace)
    load_config(c)

    _agent = c.default_agent
    _model = c.default_model
    _provider = c.default_provider

    console.clear()
    console.print(
        Panel.fit(
            "[bold cyan]SunshineAgent[/bold cyan]\n"
            "[dim]输入 prompt 开始对话  [/dim]"
            "[bold]/help[/bold] 帮助  [bold]/exit[/bold] 退出  "
            "[bold]Ctrl+C[/bold] 中断",
            title="Sunshine",
            border_style="cyan",
        )
    )

    abort = asyncio.Event()

    def _on_sigint():
        if not abort.is_set():
            abort.set()
            console.print("\n[dim]中断信号已发送，等待 agent 停止...[/dim]")

    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGINT, lambda s, f: _on_sigint())

    while True:
        try:
            abort.clear()

            status_line = (
                f"[dim]{_provider}[/dim] "
                f"[bold]{_model}[/bold]  "
                f"agent=[bold]{_agent}[/bold]  "
                f"key={_key_status(c, _provider)}"
            )
            bu = _get_base_url(c, _provider)
            if bu:
                status_line += f"  base=[dim]{bu}[/dim]"
            console.print(f"  {status_line}")

            if app_ctx._current_session_id:
                prompt_text = f"[dim]… [{app_ctx._current_session_id[:16]}][/dim] > "
            else:
                prompt_text = "[bold green]> [/bold green]"
            user_input = console.input(prompt_text)
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见[/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

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

        await _send_prompt(
            app_ctx, user_input,
            agent_name=_agent, model_id=_model, provider_id=_provider,
            steps=steps, abort=abort,
        )

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
        _print_status(c, c.default_provider)
        return None

    if action == "/apikey":
        if not args:
            console.print("[red]用法: /apikey <provider> <key>[/red]")
            return None
        parts2 = args.split(maxsplit=1)
        prov = parts2[0].lower()
        key = parts2[1] if len(parts2) > 1 else ""
        if prov == "openai":
            c.openai_api_key = key
        elif prov == "anthropic":
            c.anthropic_api_key = key
        else:
            console.print(f"[red]未知 provider: {prov}[/red]")
            return None
        ctx.provider_factory._clients.clear()
        console.print(f"[green]✓ {prov} API key 已设置[/green]")
        save_config(c)
        return None

    if action == "/baseurl":
        c.openai_base_url = args if args else None
        c.anthropic_base_url = args if args else None
        ctx.provider_factory._clients.clear()
        console.print(f"[green]✓ base_url = {args or '默认（已清除）'}[/green]")
        save_config(c)
        return None

    if action == "/model":
        if not args:
            _model_picker(ctx)
            return "reload"
        c.default_model = args
        console.print(f"[green]✓ model = {args}[/green]")
        save_config(c)
        return "reload"

    if action == "/models":
        _model_picker(ctx)
        return "reload"

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
            "直接输入内容则发送给 Agent",
            title="帮助",
            border_style="blue",
        )
    )


def _print_status(c, provider: str):
    console.print(
        Panel.fit(
            f"provider : [bold]{provider}[/bold]\n"
            f"model    : [bold]{c.default_model}[/bold]\n"
            f"agent    : [bold]{c.default_agent}[/bold]\n"
            f"api_key  : {_key_status(c, provider)}\n"
            f"base_url : {_get_base_url(c, provider) or '默认'}\n"
            f"workspace: {c.workspace_root}",
            title="状态",
            border_style="green",
        )
    )


def _model_picker(ctx):
    """交互式模型选择器。"""
    catalog = ctx.catalog
    models = catalog.models
    if not models:
        console.print("[red]没有可用模型。[/red]")
        return

    table = Table(title="选择模型 (输入编号切换)")
    table.add_column("#", style="dim")
    table.add_column("模型")
    table.add_column("Cost")
    table.add_column("Cap")

    for i, m in enumerate(models, 1):
        cur = "●" if m.model_id == ctx.config.default_model else ""
        table.add_row(
            str(i),
            f"{cur} {m.display_name} [dim]{m.model_id}[/dim]",
            m.cost_tier, m.capability_tier,
        )

    console.print(table)
    console.print("[dim]输入编号 / 关键词搜索 / Enter 取消[/dim]")
    choice = console.input("模型 > ").strip()
    if not choice:
        return
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(models):
            ctx.config.default_model = models[idx].model_id
            save_config(ctx.config)
            console.print(f"[green]✓ {models[idx].display_name}[/green]")
            return
    matches = [m for m in models
               if choice.lower() in m.model_id.lower()
               or choice.lower() in m.display_name.lower()]
    if len(matches) == 1:
        ctx.config.default_model = matches[0].model_id
        save_config(ctx.config)
        console.print(f"[green]✓ {matches[0].display_name}[/green]")
    elif len(matches) > 1:
        for j, m in enumerate(matches, 1):
            console.print(f"  [bold]{j}[/bold] {m.model_id}")
    else:
        console.print("[red]未找到匹配[/red]")


if __name__ == "__main__":
    app()
