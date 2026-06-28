"""System prompt 引擎 —— 按模型组装 system prompt。

OWNER: Human
SKILL: Jinja2 templates, message format conversion

Pipeline:
  base template → instructions(AGENTS.md) → agent_prompt → environment → skills → 拼接
"""

import os
import platform
import re
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


class SystemPromptEngine:
    """System prompt 组装引擎。

    对应 OpenCode 的 system.ts prompt pipeline：
    base | instructions → agent_prompt → environment → skills → user.system
    """

    # 模型名称 → 模板文件名 的路由规则
    _MODEL_ROUTES: list[tuple[str, str]] = [
        (r"claude", "anthropic"),
        (r"deepseek", "deepseek"),
        (r"gpt-4|o1|o3", "beast"),
        (r"gpt.*codex", "codex"),
        (r"gpt", "gpt"),
        (r"gemini", "gemini"),
        (r"trinity", "trinity"),
    ]

    # 指令文件名候选列表（按优先级搜索）
    _INSTRUCTION_FILES = ["AGENTS.md", "CONTEXT.md"]

    def __init__(self, templates_dir: str, skill_loader=None):
        self._jinja = Environment(loader=FileSystemLoader(templates_dir))
        self._skill_loader = skill_loader

    def _select_template(self, model_id: str) -> str:
        model_lower = model_id.lower()
        for pattern, name in self._MODEL_ROUTES:
            if re.search(pattern, model_lower):
                return f"{name}.txt"
        return "default.txt"

    async def build(self, agent, ctx) -> str:
        # 1. 模型模板
        template_name = self._select_template(ctx.model_id)
        template = self._jinja.get_template(template_name)
        base = template.render(
            model=ctx.model_id,
            provider=ctx.provider_id,
            date=datetime.now(UTC).strftime("%Y-%m-%d"),
        )

        # 2. 工作区指令文件（AGENTS.md 等）
        instructions = self._load_instructions(ctx.workspace) if ctx.workspace else ""

        # 3. Agent 自定义 prompt
        agent_prompt = agent.system_prompt or ""

        # 4. 环境信息
        env = self._build_environment(ctx.workspace)

        # 5. Skills 列表
        skills = ""
        if self._skill_loader:
            skills = self._skill_loader.to_system_prompt()

        # 6. 拼接
        parts = [base, instructions, agent_prompt, env, skills]
        return "\n\n".join(p for p in parts if p)

    def _load_instructions(self, workspace: str) -> str:
        """从工作区读取指令文件（AGENTS.md / CLAUDE.md / CONTEXT.md）。

        对应 OpenCode instruction.ts system() — 向上查找并注入。
        """
        root = Path(workspace)
        parts: list[str] = []
        seen: set[str] = set()

        for filename in self._INSTRUCTION_FILES:
            path = root / filename
            if path.exists() and str(path) not in seen:
                try:
                    content = path.read_text(encoding="utf-8")
                    parts.append(
                        f"Instructions from: {path}\n{content}"
                    )
                    seen.add(str(path))
                except Exception:
                    pass

        return "\n\n".join(parts)

    def _build_environment(self, workspace: str = "") -> str:
        shell = os.environ.get("SHELL", "powershell")
        cwd = workspace or os.getcwd()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        is_git = (Path(cwd) / ".git").exists()

        return (
            "<env>\n"
            f"  Working directory: {cwd}\n"
            f"  Is directory a git repo: {'yes' if is_git else 'no'}\n"
            f"  Platform: {platform.system()} {platform.release()}\n"
            f"  Shell: {shell}\n"
            f"  Date: {today}\n"
            "</env>"
        )
