"""Spawn configuration."""

from pydantic import BaseModel


class SpawnConfig(BaseModel):
    """Configuration for the engram-spawn package."""

    user_id: str = "system"
    max_subtasks: int = 10
    default_strategy: str = "auto"
    auto_route: bool = False
