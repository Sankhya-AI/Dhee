"""Resilience configuration."""

from pydantic import BaseModel


class ResilienceConfig(BaseModel):
    """Configuration for the engram-resilience package."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True
    compact_threshold_tokens: int = 4000
    compact_keep_recent: int = 5
