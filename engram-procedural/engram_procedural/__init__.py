"""engram-procedural — The Craftsman.

Procedural memory: learn, refine, and recall step-by-step procedures
from experience. Procedures improve with practice (success rate tracking),
become automatic (retrieval boost), and transfer across domains (abstraction).

Usage::

    from engram.memory.main import Memory
    from engram_procedural import Procedural, ProceduralConfig

    memory = Memory(config=...)
    proc = Procedural(memory, user_id="default")
    proc.extract_procedure(episode_ids=["id1", "id2", "id3"], name="debug_python")
"""

from engram_procedural.config import ProceduralConfig
from engram_procedural.procedural import Procedural

__all__ = ["Procedural", "ProceduralConfig"]
