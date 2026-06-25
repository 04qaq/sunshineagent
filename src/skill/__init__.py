"""Skill system loader."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    path: str
    content: str
    model: str | None = None
    mode: str = "default"


class SkillLoader:
    def __init__(self, skill_dirs: list[str]):
        self._skill_dirs = [Path(d) for d in skill_dirs]
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    async def load_all(self):
        for skill_dir in self._skill_dirs:
            if not skill_dir.exists():
                continue
            for md_file in skill_dir.glob("**/*.md"):
                await self._load_file(md_file)
        self._loaded = True

    async def _load_file(self, path: Path):
        try:
            import frontmatter

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
        except Exception:
            pass

    def list_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def to_system_prompt(self) -> str:
        skills = self.list_skills()
        if not skills:
            return ""

        lines = ["<available_skills>"]
        for skill in skills:
            lines.append("  <skill>")
            lines.append(f"    <name>{skill.name}</name>")
            lines.append(f"    <description>{skill.description}</description>")
            lines.append(f"    <location>{skill.path}</location>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)
