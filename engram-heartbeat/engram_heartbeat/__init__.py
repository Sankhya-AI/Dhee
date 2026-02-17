"""engram-heartbeat — Scheduled proactive behaviors for AI agents.

Register recurring actions (memory decay, consolidation, reminders,
health checks) that run on a cron-like schedule.

Usage::

    from engram.memory.main import Memory
    from engram_heartbeat import Heartbeat, HeartbeatConfig

    memory = Memory(config=...)
    hb = Heartbeat(memory, agent_id="claude-code")
    hb.schedule(name="nightly_decay", action="decay", interval_minutes=1440)
    hb.start()
"""

from engram_heartbeat.config import HeartbeatConfig
from engram_heartbeat.heartbeat import Heartbeat

__all__ = ["Heartbeat", "HeartbeatConfig"]
