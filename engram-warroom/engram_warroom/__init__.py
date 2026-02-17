"""engram-warroom — Multi-agent war room orchestration."""

from engram_warroom.config import WarRoomConfig
from engram_warroom.decision import DecisionState, validate_transition
from engram_warroom.warroom import WarRoom
from engram_warroom.monitor import MonitorRole
from engram_warroom.autopick import AutoPicker
from engram_warroom.failover import AutoFailover

__all__ = [
    "WarRoomConfig",
    "DecisionState",
    "validate_transition",
    "WarRoom",
    "MonitorRole",
    "AutoPicker",
    "AutoFailover",
]
