"""engram-policy — Multi-layer access control for AI agents.

Define what each agent can see, write, and do. Evaluates access requests
against layered policy rules stored as memories.

Usage::

    from engram.memory.main import Memory
    from engram_policy import PolicyEngine, PolicyConfig

    memory = Memory(config=...)
    engine = PolicyEngine(memory)
    engine.add_policy(agent_id="intern", resource="production/*", actions=["read"], effect="deny")
"""

from engram_policy.config import PolicyConfig
from engram_policy.engine import PolicyEngine

__all__ = ["PolicyEngine", "PolicyConfig"]
