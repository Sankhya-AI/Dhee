"""engram-router — Memory-as-Router for agent coordination.

Agents register capabilities as memories. Tasks get auto-routed to the
best agent via semantic search. No central coordinator — memory IS the
orchestrator.

Usage::

    from engram.memory.main import Memory
    from engram_router import AgentRegistry, TaskRouter, RouterConfig

    memory = Memory(config=...)
    config = RouterConfig()
    registry = AgentRegistry(memory, user_id=config.user_id)
    router = TaskRouter(registry, task_manager, config=config)
"""

from engram_router.config import RouterConfig
from engram_router.registry import AgentRegistry
from engram_router.router import TaskRouter

__all__ = ["AgentRegistry", "TaskRouter", "RouterConfig"]
