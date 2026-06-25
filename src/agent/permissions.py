"""Permission system for tools."""

from dataclasses import dataclass, field


@dataclass
class PermissionRuleset:
    allow_tools: set[str] = field(default_factory=set)
    deny_tools: set[str] = field(default_factory=set)
    allow_bash: bool = False
    allow_network: bool = False
    allow_file_write: bool = False
    allow_file_write_patterns: list[str] = field(default_factory=list)
    allow_mcp_tools: set[str] = field(default_factory=set)
    readonly: bool = False
    write_plan: bool = False

    @classmethod
    def all(cls) -> "PermissionRuleset":
        return cls(
            allow_bash=True,
            allow_network=True,
            allow_file_write=True,
        )

    @classmethod
    def default(cls) -> "PermissionRuleset":
        return cls(
            allow_bash=True,
            allow_network=True,
            allow_file_write=True,
            deny_tools={"task", "skill"},
        )

    @classmethod
    def read_only(cls) -> "PermissionRuleset":
        return cls(
            allow_tools={"read", "glob", "grep", "lsp"},
            allow_bash=False,
            allow_network=True,
            allow_file_write=False,
            readonly=True,
        )

    @classmethod
    def model_only(cls) -> "PermissionRuleset":
        return cls(
            deny_tools={"*"},
        )

    def can_use(self, tool_name: str) -> bool:
        if "*" in self.deny_tools or tool_name in self.deny_tools:
            return False
        if tool_name in self.allow_tools:
            return True
        return self.allow_bash or self.allow_file_write
