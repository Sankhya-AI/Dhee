"""engram-working — The Blackboard.

Working memory: explicit short-term buffer with capacity limits (Miller's Law,
default 7). Items have activation levels that decay in minutes. Accessing an
item refreshes it. Overflow pushes least-active items to long-term memory.

Usage::

    from engram.memory.main import Memory
    from engram_working import WorkingMemory, WorkingMemoryConfig

    memory = Memory(config=...)
    wm = WorkingMemory(memory, user_id="default")
    wm.push("Current task: fix the login bug", tag="task")
"""

from engram_working.config import WorkingMemoryConfig
from engram_working.working import WorkingMemory

__all__ = ["WorkingMemory", "WorkingMemoryConfig"]
