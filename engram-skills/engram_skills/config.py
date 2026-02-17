"""Skills configuration."""

from pydantic import BaseModel


class SkillConfig(BaseModel):
    """Configuration for the engram-skills package."""

    user_id: str = "system"
    max_skills: int = 500
    allow_remote_invoke: bool = False
