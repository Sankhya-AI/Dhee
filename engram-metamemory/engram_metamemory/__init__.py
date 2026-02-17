"""engram-metamemory — The Oracle.

Confidence scoring, feeling-of-knowing, knowledge gap tracking,
and retrieval calibration for Engram memories.

Usage::

    from engram.memory.main import Memory
    from engram_metamemory import Metamemory, MetamemoryConfig

    memory = Memory(config=...)
    mm = Metamemory(memory, user_id="default")
    fok = mm.feeling_of_knowing("quantum computing")
"""

from engram_metamemory.config import MetamemoryConfig
from engram_metamemory.metamemory import Metamemory

__all__ = ["Metamemory", "MetamemoryConfig"]
