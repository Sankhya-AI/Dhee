"""engram — biologically-inspired memory for AI agents.

- FadeMem: Dual-layer (SML/LML) with natural decay
- EchoMem: Multi-modal encoding for stronger retention
- CategoryMem: Dynamic hierarchical category organization

Quick Start (zero-config, no API key):
    from engram import Memory
    m = Memory()
    m.add("User prefers Python")
    results = m.search("programming preferences")

Tiered Memory Classes:
    CoreMemory   — lightweight: add/search/delete + decay (no LLM)
    SmartMemory  — + echo encoding, categories, knowledge graph (needs LLM)
    FullMemory   — + scenes, profiles, tasks, projects (everything)
    Memory       — alias for CoreMemory (lightest default)
"""

from engram.memory.core import CoreMemory
from engram.memory.smart import SmartMemory
from engram.memory.main import FullMemory
from engram.simple import Engram
from engram.core.category import CategoryProcessor, Category, CategoryType, CategoryMatch
from engram.core.echo import EchoProcessor, EchoDepth, EchoResult
from engram.configs.base import MemoryConfig, FadeMemConfig, EchoMemConfig, CategoryMemConfig, ScopeConfig

# Default: CoreMemory (lightest, zero-config)
Memory = CoreMemory

__version__ = "0.6.0"
__all__ = [
    # Tiered memory classes
    "CoreMemory",
    "SmartMemory",
    "FullMemory",
    "Memory",
    # Simplified interface
    "Engram",
    # CategoryMem
    "CategoryProcessor",
    "Category",
    "CategoryType",
    "CategoryMatch",
    # EchoMem
    "EchoProcessor",
    "EchoDepth",
    "EchoResult",
    # Config
    "MemoryConfig",
    "FadeMemConfig",
    "EchoMemConfig",
    "CategoryMemConfig",
    "ScopeConfig",
]
