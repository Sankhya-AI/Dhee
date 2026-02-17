"""engram-identity — Agent identity management.

Store and discover agent personas, roles, goals, and communication styles
as Engram memories. Other agents discover identities via semantic search.

Usage::

    from engram.memory.main import Memory
    from engram_identity import Identity, IdentityConfig

    memory = Memory(config=...)
    identity = Identity(memory, agent_id="claude-code")
    identity.declare(name="Claude Code", role="Senior software engineer", goals=["write clean code"])
"""

from engram_identity.config import IdentityConfig
from engram_identity.identity import Identity

__all__ = ["Identity", "IdentityConfig"]
