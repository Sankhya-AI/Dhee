"""Policy configuration."""

from pydantic import BaseModel


class PolicyConfig(BaseModel):
    """Configuration for the engram-policy package."""

    user_id: str = "system"
    default_effect: str = "deny"
    token_ttl_minutes: int = 60
    max_policies: int = 500
