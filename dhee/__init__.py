"""dhee — The cognition layer that turns any agent into a HyperAgent.

4 operations. Deferred enrichment. Minimal hot-path cost.
Your agent remembers, learns from outcomes, and predicts what you need next.

Quick Start:
    from dhee import Dhee

    d = Dhee()
    d.remember("User prefers dark mode")
    d.recall("what theme does the user like?")
    d.context("fixing auth bug")
    d.checkpoint("Fixed it", what_worked="git blame first")

Memory Classes:
    Engram       — batteries-included memory interface with sensible defaults
    CoreMemory   — lightweight: add/search/delete + decay (no LLM)
    SmartMemory  — + echo encoding, categories, knowledge graph (needs LLM)
    FullMemory   — + scenes, profiles, orchestration, cognition (everything)
    Memory       — alias for CoreMemory (lightest default)
"""

from dhee.memory.core import CoreMemory
from dhee.memory.smart import SmartMemory
from dhee.memory.main import FullMemory
from dhee.simple import Dhee, Engram
from dhee.plugin import DheePlugin
from dhee.core.category import CategoryProcessor, Category, CategoryType, CategoryMatch
from dhee.core.echo import EchoProcessor, EchoDepth, EchoResult
from dhee.configs.base import MemoryConfig, FadeMemConfig, EchoMemConfig, CategoryMemConfig, ScopeConfig

# Default: CoreMemory (lightest, zero-config)
Memory = CoreMemory

__version__ = "3.3.0"
__all__ = [
    # Memory classes
    "Engram",
    "CoreMemory",
    "SmartMemory",
    "FullMemory",
    "Memory",
    # Simplified interface (the 4-operation API)
    "Dhee",
    # Universal plugin
    "DheePlugin",
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
