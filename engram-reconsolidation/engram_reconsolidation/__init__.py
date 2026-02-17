"""engram-reconsolidation — The Updater.

Memory reconsolidation: when a memory is retrieved, current context can
propose updates. Updates go through conflict resolution. Full version
history via the existing memory_history table.

Usage::

    from engram.memory.main import Memory
    from engram_reconsolidation import Reconsolidation, ReconsolidationConfig

    memory = Memory(config=...)
    rc = Reconsolidation(memory, user_id="default")
    proposal = rc.propose_update(memory_id="abc", new_context="Updated info...")
"""

from engram_reconsolidation.config import ReconsolidationConfig
from engram_reconsolidation.reconsolidation import Reconsolidation

__all__ = ["Reconsolidation", "ReconsolidationConfig"]
