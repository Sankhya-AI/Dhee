"""engram-spawn — Task decomposition and delegation for AI agents.

Break complex tasks into sub-tasks stored in memory. Other agents pick
them up through the router.

Usage::

    from engram.memory.main import Memory
    from engram_spawn import Spawner, SpawnConfig

    memory = Memory(config=...)
    spawner = Spawner(memory)
    subtasks = spawner.decompose(task_id, strategy="parallel")
"""

from engram_spawn.config import SpawnConfig
from engram_spawn.spawner import Spawner

__all__ = ["Spawner", "SpawnConfig"]
