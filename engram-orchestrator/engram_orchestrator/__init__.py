"""engram-orchestrator — Memory-as-Orchestrator coordination layer.

Agents register capabilities as memories. Tasks get auto-routed to the
best agent via semantic search. No new DB tables — everything is a memory.

Usage::

    from engram.memory.main import Memory
    from engram_bus import Bus
    from engram_orchestrator import Coordinator

    memory = Memory(config=...)
    bus = Bus()

    coordinator = Coordinator(memory, bus, config)
    coordinator.registry.register("claude-code", capabilities=["python", "debugging"], ...)
    coordinator.start()
"""

from engram_orchestrator.coordinator import Coordinator
from engram_orchestrator.registry import AgentRegistry
from engram_orchestrator.router import TaskRouter

__all__ = ["AgentRegistry", "TaskRouter", "Coordinator"]
