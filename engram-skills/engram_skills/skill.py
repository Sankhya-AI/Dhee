"""Skill model — definition, parameters, and examples."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Skill:
    """A registered skill/tool definition."""

    name: str
    description: str
    parameters: dict[str, str] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    agent_id: str = ""
    tags: list[str] = field(default_factory=list)
    callable: Callable | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict (excludes callable)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "examples": self.examples,
            "agent_id": self.agent_id,
            "tags": self.tags,
        }
