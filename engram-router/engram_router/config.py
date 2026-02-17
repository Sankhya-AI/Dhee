"""Router configuration."""

from pydantic import BaseModel


class RouterConfig(BaseModel):
    """Configuration for the engram-router package."""

    auto_route: bool = True
    auto_execute: bool = False
    similarity_weight: float = 0.7
    availability_weight: float = 0.3
    log_events: bool = True
    user_id: str = "system"
    fallback_agent: str = ""
