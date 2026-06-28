"""SunshineAgent TUI 主应用。

布局：
┌──────────────────────────────────────┬─────────────────────┐
│          Chat Area                   │   Context Panel     │
│                                      │                     │
│  □ Build · GPT-4 · 12.3s             │   Project           │
│                                      │   SunshineAgent     │
│  用户消息...                          │                     │
│                                      │   Context           │
│  助手回复...                          │   187k tokens 18%   │
│    → Read src/main.py                │   $0.02             │
│    → Edit renderer.py                │                     │
│                                      │   Git               │
│                                      │   master            │
│                                      │   3 modified        │
├──────────────────────────────────────┤                     │
│ > _                                  │                     │
└──────────────────────────────────────┴─────────────────────┘
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Awaitable, Callable

from src.cli.tui.message import Message, TextPart, ThinkingPart, ToolPart, ToolStatus
from src.cli.tui.state import StateManager


# ── 颜色常量 ──────────────────────────────────────────────────────────

class Colors:
    """ANSI 颜色。"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # 前景色
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # 亮色
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_CYAN = "\033[96m"

    # 灰色
    GRAY = "\033[90m"


# ── 工具图标和颜色 ────────────────────────────────────────────────────

TOOL_CONFIG = {
    "bash": {"icon": "$", "color": Colors.WHITE},
    "read": {"icon": "→", "color": Colors.CYAN},
    "write": {"icon": "←", "color": Colors.YELLOW},
    "edit": {"icon": "←", "color": Colors.YELLOW},
    "glob": {"icon": "✱", "color": Colors.CYAN},
    "grep": {"icon": "✱", "color": Colors.CYAN},
    "webfetch": {"icon": "%", "color": Colors.BLUE},
    "websearch": {"icon": "%", "color": Colors.BLUE},
    "task": {"icon": "│", "color": Colors.CYAN},
    "todowrite": {"icon": "⚙", "color": Colors.WHITE},
    "question": {"icon": "→", "color": Colors.BLUE},
}

DEFAULT_TOOL_CONFIG = {"icon": "⚙", "color": Colors.WHITE}


# ── Agent 颜色 ────────────────────────────────────────────────────────

AGENT_COLORS = {
    "build": Colors.CYAN,
    "plan": Colors.BLUE,
    "general": Colors.WHITE,
    "explore": Colors.GREEN,
    "code": Colors.YELLOW,
    "test": Colors.MAGENTA,
    "document": Colors.WHITE,
}


# ── 辅助函数 ──────────────────────────────────────────────────────────

def get_terminal_size() -> tuple[int, int]:
    """获取终端大小。"""
    try:
        import shutil
        size = shutil.get_terminal_size()
        return size.columns, size.lines
    except Exception:
        return 80, 24


def strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列。"""
    import re
    return re.sub(r'\033\[[0-9;]*m', '', text)


def visible_len(text: str) -> str:
    """获取可见长度（去除 ANSI）。"""
    return len(strip_ansi(text))


def truncate(text: str, max_len: int) -> str:
    """截断文本。"""
    if visible_len(text) <= max_len:
        return text
    result = []
    current_len = 0
    in_escape = False
    for char in text:
        if char == '\033':
            in_escape = True
            result.append(char)
            continue
        if in_escape:
            result.append(char)
            if char == 'm':
                in_escape = False
            continue
        if current_len >= max_len - 3:
            break
        result.append(char)
        current_len += 1
    return ''.join(result) + "..."


def format_duration(seconds: float) -> str:
    """格式化时长。"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"


# ── SunshineTUI ───────────────────────────────────────────────────────

class SunshineTUI:
    """SunshineAgent TUI 应用。"""

    def __init__(
        self,
        agent_name: str = "build",
        model_name: str = "",
        on_prompt: Callable[[str], Awaitable[None]] | None = None,
        on_interrupt: Callable[[], None] | None = None,
        on_new_session: Callable[[], Awaitable[None]] | None = None,
        on_resume_session: Callable[[str], Awaitable[None]] | None = None,
        on_switch_model: Callable[[str], Awaitable[None]] | None = None,
        on_switch_agent: Callable[[str], Awaitable[None]] | None = None,
        registry: Any = None,
        agent_registry: Any = None,
        session_service: Any = None,
        db: Any = None,
        workspace: str = "",
    ):
        self._agent_name = agent_name
        self._model_name = model_name
        self._on_prompt = on_prompt
        self._on_interrupt = on_interrupt
        self._on_new_session = on_new_session
        self._on_resume_session = on_resume_session
        self._on_switch_model = on_switch_model
        self._on_switch_agent = on_switch_agent
        self._registry = registry
        self._agent_registry = agent_registry
        self._session_service = session_service
        self._db = db
        self._workspace = workspace

        # 状态管理
        self._state = StateManager()
        self._state.agent = agent_name
        self._state.model = model_name

        # 输入历史
        self._history: list[str] = []
        self._history_index: int | None = None
        self._draft: str = ""

        # 动画状态
        self._thinking_dots = 0
        self._last_render_time = 0

        # 注册更新回调
        self._state.on_update(self._on_state_update)

    def _on_state_update(self) -> None:
        """状态更新回调。"""
        # 可以在这里实现实时刷新
        pass

    async def run(self) -> None:
        """运行 TUI。"""
        # 初始化
        self._init_terminal()

        # 显示欢迎消息
        self._state.add_system_message("SunshineAgent 已启动")
        self._state.add_system_message("输入提示开始对话，/help 查看帮助")

        # 主循环
        while True:
            try:
                self._render()
                user_input = await self._get_input()

                if not user_input:
                    continue

                # 处理命令
                if user_input.startswith("/"):
                    if await self._handle_command(user_input):
                        break
                    continue

                # 处理提示
                await self._process_prompt(user_input)

            except KeyboardInterrupt:
                if self._state.phase == "running":
                    self._handle_interrupt()
                else:
                    break
            except EOFError:
                break

        self._cleanup_terminal()

    def _init_terminal(self) -> None:
        """初始化终端。"""
        # 清屏
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

    def _cleanup_terminal(self) -> None:
        """清理终端。"""
        # 显示光标
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

    def _render(self) -> None:
        """渲染界面。"""
        cols, rows = get_terminal_size()

        # 计算左右分栏
        chat_width = int(cols * 0.72)
        panel_width = cols - chat_width - 1

        # 清屏
        sys.stdout.write("\033[H")

        # 渲染每一行
        lines = []

        # 顶部：Session 信息
        lines.append(self._render_session_header(chat_width))

        # 空行
        lines.append("")

        # 聊天内容
        chat_lines = self._render_chat_area(chat_width, rows - 5)
        lines.extend(chat_lines)

        # 填充空行
        while len(lines) < rows - 2:
            lines.append("")

        # 底部输入框
        lines.append(self._render_input_bar(cols))

        # 渲染右侧 Context Panel
        panel_lines = self._render_context_panel(panel_width)

        # 合并左右
        output = []
        for i, line in enumerate(lines):
            # 左侧内容
            left = line[:chat_width] if len(line) > chat_width else line
            left_padded = left + " " * max(0, chat_width - visible_len(left))

            # 右侧内容
            right = panel_lines[i] if i < len(panel_lines) else ""
            right_padded = right + " " * max(0, panel_width - visible_len(right))

            # 分隔线
            separator = f"{Colors.GRAY}│{Colors.RESET}"

            output.append(f"{left_padded}{separator}{right_padded}")

        # 写入终端
        sys.stdout.write("\n".join(output))
        sys.stdout.flush()

    def _render_session_header(self, width: int) -> str:
        """渲染 Session 头部。"""
        agent = self._state.agent
        model = self._state.model
        duration = self._state.duration

        # 图标
        icon = f"{Colors.CYAN}□{Colors.RESET}"

        # Agent 名称
        agent_color = AGENT_COLORS.get(agent, Colors.WHITE)
        agent_text = f"{agent_color}{agent.title()}{Colors.RESET}"

        # 模型
        model_text = f"{Colors.DIM}{model}{Colors.RESET}" if model else ""

        # 时长
        duration_text = ""
        if self._state.phase == "running" and self._state.start_time:
            elapsed = time.time() - self._state.start_time
            duration_text = f"{Colors.DIM}{format_duration(elapsed)}{Colors.RESET}"
        elif duration:
            duration_text = f"{Colors.DIM}{duration}{Colors.RESET}"

        # 组合
        parts = [icon, agent_text]
        if model_text:
            parts.append(f"{Colors.DIM}·{Colors.RESET}")
            parts.append(model_text)
        if duration_text:
            parts.append(f"{Colors.DIM}·{Colors.RESET}")
            parts.append(duration_text)

        return " ".join(parts)

    def _render_chat_area(self, width: int, max_lines: int) -> list[str]:
        """渲染聊天区域。"""
        lines = []

        for msg in self._state.messages:
            if msg.role == "user":
                lines.extend(self._render_user_message(msg, width))
            elif msg.role == "assistant":
                lines.extend(self._render_assistant_message(msg, width))
            elif msg.role == "system":
                lines.extend(self._render_system_message(msg, width))

            # 空行分隔
            lines.append("")

        # 限制行数
        if len(lines) > max_lines:
            lines = lines[-max_lines:]

        return lines

    def _render_user_message(self, msg: Message, width: int) -> list[str]:
        """渲染用户消息。"""
        lines = []
        agent_color = AGENT_COLORS.get(msg.agent, Colors.GREEN)

        for part in msg.parts:
            if isinstance(part, TextPart):
                # 用户输入带边框
                text = part.text
                border = f"{agent_color}┌{'─' * (visible_len(text) + 4)}┐{Colors.RESET}"
                content = f"{agent_color}│{Colors.RESET} {text}  {agent_color}│{Colors.RESET}"
                bottom = f"{agent_color}└{'─' * (visible_len(text) + 4)}┘{Colors.RESET}"
                lines.append(border)
                lines.append(content)
                lines.append(bottom)

        return lines

    def _render_assistant_message(self, msg: Message, width: int) -> list[str]:
        """渲染助手消息。"""
        lines = []

        for part in msg.parts:
            if isinstance(part, TextPart):
                # 文本内容
                text = part.text
                for line in text.split("\n"):
                    lines.append(f"  {line}")

            elif isinstance(part, ThinkingPart):
                # 思考过程
                if part.status == ToolStatus.RUNNING:
                    dots = "." * (self._thinking_dots % 4)
                    lines.append(f"  {Colors.YELLOW}+ Thinking: {dots}{Colors.RESET}")
                elif part.status == ToolStatus.COMPLETED:
                    duration = format_duration(part.end_time - part.start_time) if part.end_time else ""
                    lines.append(f"  {Colors.DIM}+ Thought: {duration}{Colors.RESET}")

            elif isinstance(part, ToolPart):
                # 工具调用
                config = TOOL_CONFIG.get(part.tool_name, DEFAULT_TOOL_CONFIG)
                icon = config["icon"]
                color = config["color"]

                if part.status == ToolStatus.RUNNING:
                    # 运行中
                    lines.append(f"  {color}{icon} {self._format_tool_input(part)}{Colors.RESET}")

                    # 实时输出
                    if part.output:
                        for line in part.output.strip().split("\n")[-3:]:
                            lines.append(f"    {Colors.DIM}{line}{Colors.RESET}")

                elif part.status == ToolStatus.COMPLETED:
                    # 完成
                    lines.append(f"  {Colors.GREEN}{icon} {self._format_tool_input(part)}{Colors.RESET}")

                elif part.status == ToolStatus.ERROR:
                    # 失败
                    lines.append(f"  {Colors.RED}{icon} {self._format_tool_input(part)}{Colors.RESET}")
                    if part.error:
                        lines.append(f"    {Colors.RED}{part.error}{Colors.RESET}")

        # 完成标记
        if msg.completed:
            agent = msg.agent.title()
            model = msg.model
            duration = format_duration(msg.duration) if msg.duration else ""
            agent_color = AGENT_COLORS.get(msg.agent, Colors.WHITE)
            lines.append(f"  {agent_color}▣ {agent}{Colors.RESET} {Colors.DIM}· {model} · {duration}{Colors.RESET}")

        return lines

    def _render_system_message(self, msg: Message, width: int) -> list[str]:
        """渲染系统消息。"""
        lines = []
        for part in msg.parts:
            if isinstance(part, TextPart):
                lines.append(f"  {Colors.DIM}# {part.text}{Colors.RESET}")
        return lines

    def _format_tool_input(self, tool: ToolPart) -> str:
        """格式化工具输入。"""
        name = tool.tool_name
        params = tool.input

        if name == "bash":
            cmd = params.get("command", "")
            return f"Bash {cmd}"
        elif name == "read":
            path = params.get("filePath", "")
            return f"Read {path}"
        elif name == "write":
            path = params.get("filePath", "")
            return f"Write {path}"
        elif name == "edit":
            path = params.get("filePath", "")
            return f"Edit {path}"
        elif name == "glob":
            pattern = params.get("pattern", "")
            return f"Glob {pattern}"
        elif name == "grep":
            pattern = params.get("pattern", "")
            return f"Grep {pattern}"
        elif name == "task":
            desc = params.get("description", "")
            return f"Task {desc}"
        else:
            return name.title()

    def _render_input_bar(self, width: int) -> str:
        """渲染输入框。"""
        agent_color = AGENT_COLORS.get(self._agent_name, Colors.GREEN)
        return f"{agent_color}>{Colors.RESET} "

    def _render_context_panel(self, width: int) -> list[str]:
        """渲染右侧 Context Panel。"""
        lines = []
        state = self._state

        # Project
        lines.append(f"{Colors.BOLD}Project{Colors.RESET}")
        lines.append(f"{Colors.DIM}{state.project_name}{Colors.RESET}")
        lines.append("")

        # Context
        lines.append(f"{Colors.BOLD}Context{Colors.RESET}")
        if state.tokens_used > 0:
            pct = int(state.tokens_used / state.tokens_limit * 100)
            tokens_k = state.tokens_used // 1000
            lines.append(f"{Colors.DIM}{tokens_k}k tokens{Colors.RESET}  {Colors.CYAN}{pct}%{Colors.RESET}")
            if state.cost > 0:
                lines.append(f"{Colors.DIM}${state.cost:.2f}{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}No context{Colors.RESET}")
        lines.append("")

        # Agent
        lines.append(f"{Colors.BOLD}Agent{Colors.RESET}")
        agent_color = AGENT_COLORS.get(state.agent, Colors.WHITE)
        lines.append(f"{agent_color}{state.agent.title()}{Colors.RESET}")
        if state.model:
            lines.append(f"{Colors.DIM}{state.model}{Colors.RESET}")
        lines.append("")

        # Git
        lines.append(f"{Colors.BOLD}Git{Colors.RESET}")
        if state.git_branch:
            lines.append(f"{Colors.DIM}{state.git_branch}{Colors.RESET}")
            if state.git_modified > 0:
                lines.append(f"{Colors.YELLOW}{state.git_modified} modified{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}Not a git repo{Colors.RESET}")
        lines.append("")

        # Modified Files
        if state.modified_files:
            lines.append(f"{Colors.BOLD}Modified Files{Colors.RESET}")
            for f in state.modified_files[:5]:
                name = f.get("name", "")
                added = f.get("added", 0)
                removed = f.get("removed", 0)
                parts = [f"{Colors.DIM}{name}{Colors.RESET}"]
                if added:
                    parts.append(f"{Colors.GREEN}+{added}{Colors.RESET}")
                if removed:
                    parts.append(f"{Colors.RED}-{removed}{Colors.RESET}")
                lines.append(" ".join(parts))
            lines.append("")

        # Tools
        lines.append(f"{Colors.BOLD}Tools{Colors.RESET}")
        active = state.active_tools
        if active:
            for tool in active[:3]:
                config = TOOL_CONFIG.get(tool.tool_name, DEFAULT_TOOL_CONFIG)
                lines.append(f"{config['color']}{config['icon']} {tool.tool_name}{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}Idle{Colors.RESET}")

        return lines

    # ── 输入处理 ──────────────────────────────────────────────────────

    async def _get_input(self) -> str:
        """获取用户输入。"""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, input)
        except (EOFError, KeyboardInterrupt):
            raise

    async def _process_prompt(self, text: str) -> None:
        """处理用户提示。"""
        # 添加到历史
        self._push_history(text)

        # 添加用户消息
        self._state.add_user_message(text, self._agent_name)

        # 设置运行状态
        self._state.set_running()

        try:
            if self._on_prompt:
                await self._on_prompt(text)
        except Exception as e:
            self._state.add_system_message(f"错误: {e}")
        finally:
            self._state.set_idle()

    def _handle_interrupt(self) -> None:
        """处理中断。"""
        self._state.add_system_message("中断信号已发送")
        if self._on_interrupt:
            self._on_interrupt()

    async def _handle_command(self, cmd: str) -> bool:
        """处理命令。"""
        parts = cmd.strip().split(maxsplit=1)
        action = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if action in ("/exit", "/quit", "/q"):
            self._state.add_system_message("再见")
            return True

        if action == "/help":
            help_text = """可用命令:
  /help          显示帮助
  /exit          退出
  /clear         新会话
  /status        显示配置
  /model         列出模型
  /model <id>    切换模型
  /agent         列出 Agent
  /agent <name>  切换 Agent"""
            self._state.add_system_message(help_text)
            return False

        if action == "/clear":
            self._state.clear()
            self._state.add_system_message("新会话已创建")
            if self._on_new_session:
                await self._on_new_session()
            return False

        if action == "/status":
            status = f"""当前配置:
  Provider: {self._registry.default_provider if self._registry else 'N/A'}
  Model:    {self._registry.default_model if self._registry else 'N/A'}
  Agent:    {self._agent_name}"""
            self._state.add_system_message(status)
            return False

        if action == "/model":
            if not args:
                await self._list_models()
                return False
            await self._switch_model(args)
            return False

        if action == "/agent":
            if not args:
                await self._list_agents()
                return False
            await self._switch_agent(args)
            return False

        self._state.add_system_message(f"未知命令: {action}")
        return False

    # ── 消息接口 ──────────────────────────────────────────────────────

    def add_message(self, role: str, content: str) -> None:
        """添加消息。"""
        if role == "user":
            self._state.add_user_message(content)
        elif role == "assistant":
            msg = self._state.add_assistant_message()
            msg.parts.append(TextPart(text=content))
            msg.completed = True
            self._state.notify_update()
        elif role == "system":
            self._state.add_system_message(content)
        elif role == "error":
            self._state.add_system_message(f"错误: {content}")

    def start_assistant_message(self) -> Message:
        """开始助手消息。"""
        return self._state.add_assistant_message()

    def update_assistant_text(self, msg: Message, text: str) -> None:
        """更新助手文本。"""
        if msg.parts and isinstance(msg.parts[-1], TextPart):
            msg.parts[-1].text += text
        else:
            msg.parts.append(TextPart(text=text))
        self._state.notify_update()

    def complete_assistant_message(self, msg: Message) -> None:
        """完成助手消息。"""
        msg.completed = True
        self._state.notify_update()

    def register_tool_start(self, tool_call_id: str, tool_name: str, params: dict) -> None:
        """注册工具开始。"""
        self._state.tool_start(tool_call_id, tool_name, params)

    def update_tool_output(self, tool_call_id: str, output: str) -> None:
        """更新工具输出。"""
        self._state.tool_output(tool_call_id, output)

    def register_tool_complete(self, tool_call_id: str, output: str) -> None:
        """注册工具完成。"""
        self._state.tool_complete(tool_call_id, output)

    def register_tool_error(self, tool_call_id: str, error: str) -> None:
        """注册工具失败。"""
        self._state.tool_error(tool_call_id, error)

    def update_status(self, **kwargs: Any) -> None:
        """更新状态。"""
        if "model" in kwargs:
            self._state.model = kwargs["model"]
        if "agent" in kwargs:
            self._state.agent = kwargs["agent"]
        self._state.notify_update()

    # ── 历史记录 ──────────────────────────────────────────────────────

    def _push_history(self, text: str) -> None:
        """添加到历史。"""
        text = text.strip()
        if not text:
            return
        if self._history and self._history[-1] == text:
            return
        self._history.append(text)
        if len(self._history) > 200:
            self._history = self._history[-200:]
        self._history_index = None

    # ── 模型/Agent 命令 ───────────────────────────────────────────────

    async def _list_models(self) -> None:
        """列出模型。"""
        if not self._registry:
            self._state.add_system_message("Provider registry 未初始化")
            return

        lines = ["可用模型:\n"]
        for pid in self._registry.providers:
            models = self._registry.list_models(pid)
            for m in models:
                marker = "→" if m.full_id == self._registry.default_model else " "
                lines.append(f"  {marker} {m.full_id} - {m.name}")

        lines.append(f"\n当前: {self._registry.default_model}")
        self._state.add_system_message("\n".join(lines))

    async def _switch_model(self, model_id: str) -> None:
        """切换模型。"""
        if not self._registry:
            self._state.add_system_message("Provider registry 未初始化")
            return

        m = self._registry.resolve(model_id)
        if not m:
            self._state.add_system_message(f"模型未找到: {model_id}")
            return

        self._registry.default_model = m.full_id
        self._registry.default_provider = m.provider
        self._registry.save()

        self._model_name = m.full_id
        self._state.model = m.full_id
        self._state.provider = m.provider
        self._state.notify_update()

        self._state.add_system_message(f"✓ 已切换到 {m.name} ({m.full_id})")

        if self._on_switch_model:
            await self._on_switch_model(m.full_id)

    async def _list_agents(self) -> None:
        """列出 Agent。"""
        if not self._agent_registry:
            self._state.add_system_message("Agent registry 未初始化")
            return

        agents = self._agent_registry.list(include_hidden=False)
        lines = ["可用 Agent:\n"]
        for a in agents:
            marker = "→" if a.name == self._agent_name else " "
            lines.append(f"  {marker} {a.name} ({a.mode})")

        self._state.add_system_message("\n".join(lines))

    async def _switch_agent(self, agent_name: str) -> None:
        """切换 Agent。"""
        if not self._agent_registry:
            self._state.add_system_message("Agent registry 未初始化")
            return

        agent = self._agent_registry.get(agent_name)
        if not agent:
            self._state.add_system_message(f"未知 Agent: {agent_name}")
            return

        self._agent_name = agent_name
        self._state.agent = agent_name
        self._state.notify_update()

        self._state.add_system_message(f"✓ agent = {agent_name}")

        if self._on_switch_agent:
            await self._on_switch_agent(agent_name)


# ── 便捷函数 ──────────────────────────────────────────────────────────

async def run_tui(
    on_prompt: Callable[[str], Awaitable[None]],
    agent_name: str = "build",
    model_name: str = "",
    on_interrupt: Callable[[], None] | None = None,
    on_new_session: Callable[[], Awaitable[None]] | None = None,
    on_resume_session: Callable[[str], Awaitable[None]] | None = None,
    on_switch_model: Callable[[str], Awaitable[None]] | None = None,
    on_switch_agent: Callable[[str], Awaitable[None]] | None = None,
    registry: Any = None,
    agent_registry: Any = None,
    session_service: Any = None,
    db: Any = None,
    workspace: str = "",
) -> None:
    """运行 TUI。"""
    app = SunshineTUI(
        agent_name=agent_name,
        model_name=model_name,
        on_prompt=on_prompt,
        on_interrupt=on_interrupt,
        on_new_session=on_new_session,
        on_resume_session=on_resume_session,
        on_switch_model=on_switch_model,
        on_switch_agent=on_switch_agent,
        registry=registry,
        agent_registry=agent_registry,
        session_service=session_service,
        db=db,
        workspace=workspace,
    )
    await app.run()
