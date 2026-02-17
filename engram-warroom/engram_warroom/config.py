"""War room configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WarRoomConfig:
    """Configuration for the war room orchestration layer."""

    enabled: bool = False
    auto_pick: bool = True              # Auto-pick top task on /start
    auto_failover: bool = True          # Auto-switch agent on rate limit
    monitor_agent: str = ""             # Default monitor agent name
    decision_timeout_minutes: int = 30
    max_participants: int = 10
