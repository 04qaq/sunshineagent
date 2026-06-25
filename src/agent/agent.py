"""Agent registry and AgentInfo definition.

Ownership: Human module. This is a stub until the human implements it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.permissions import PermissionRuleset


@dataclass
class AgentInfo:
    name: str
    mode: str = "primary"
    native: bool = True
    hidden: bool = False

    permission: PermissionRuleset | None = None

    system_prompt: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_steps: int | None = None
