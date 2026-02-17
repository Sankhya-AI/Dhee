"""Heartbeat configuration."""

from pydantic import BaseModel


class HeartbeatConfig(BaseModel):
    """Configuration for the engram-heartbeat package."""

    user_id: str = "system"
    tick_interval_seconds: float = 60.0
    max_behaviors: int = 50
    log_runs: bool = True
