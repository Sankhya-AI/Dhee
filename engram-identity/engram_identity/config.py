"""Identity configuration."""

from pydantic import BaseModel


class IdentityConfig(BaseModel):
    """Configuration for the engram-identity package."""

    user_id: str = "system"
    auto_inject: bool = True
    max_discover_results: int = 10
