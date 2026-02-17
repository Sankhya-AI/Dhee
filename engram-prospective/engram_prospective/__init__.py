"""engram-prospective — The Planner.

Prospective memory: remembering the future. Store intentions with
time, event, or condition triggers that fire when conditions are met.

Usage::

    from engram.memory.main import Memory
    from engram_prospective import Prospective, ProspectiveConfig

    memory = Memory(config=...)
    pm = Prospective(memory, user_id="default")
    pm.add_intention(
        description="Send weekly report",
        trigger_type="time",
        trigger_value="2025-01-20T09:00:00Z",
        action="remind user to send weekly report",
    )
"""

from engram_prospective.config import ProspectiveConfig
from engram_prospective.prospective import Prospective

__all__ = ["Prospective", "ProspectiveConfig"]
