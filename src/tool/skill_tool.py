"""Skill tool: load a skill by name."""

from src.tool.base import Tool, ToolContext, ToolResult


class SkillTool(Tool):
    name = "skill"
    description = "Load a specialized skill that provides domain-specific instructions."
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The name of the skill from available_skills",
            },
        },
        "required": ["name"],
    }

    def __init__(self, skill_loader):
        self._skill_loader = skill_loader

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        name = params["name"]
        skill = self._skill_loader.get(name)
        if not skill:
            return ToolResult(output=f"Skill not found: {name}")
        return ToolResult(
            output=f"<skill_content name=\"{skill.name}\">\n{skill.content}\n</skill_content>"
        )
