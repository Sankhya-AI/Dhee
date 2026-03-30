"""dhee — Cognition as a Service. The memory layer that makes ANY agent intelligent.

- FadeMem: Dual-layer (SML/LML) with natural decay
- EchoMem: Multi-modal encoding for stronger retention
- CategoryMem: Dynamic hierarchical category organization
- Universal Engram: Structured facts + context anchoring
- Cognition Engine: Memory-grounded recursive reasoning
- Prospective Scenes: Memory-driven future anticipation

Quick Start (zero-config, no API key):
    from dhee import Memory
    m = Memory()
    m.add("User prefers Python")
    results = m.search("programming preferences")

Tiered Memory Classes:
    CoreMemory   — lightweight: add/search/delete + decay (no LLM)
    SmartMemory  — + echo encoding, categories, knowledge graph (needs LLM)
    FullMemory   — + scenes, profiles, tasks, cognition (everything)
    Memory       — alias for CoreMemory (lightest default)
"""

from dhee.memory.core import CoreMemory
from dhee.memory.smart import SmartMemory
from dhee.memory.main import FullMemory
from dhee.simple import Engram, Dhee
from dhee.adapters.base import DheePlugin
from dhee.core.category import CategoryProcessor, Category, CategoryType, CategoryMatch
from dhee.core.echo import EchoProcessor, EchoDepth, EchoResult
from dhee.configs.base import MemoryConfig, FadeMemConfig, EchoMemConfig, CategoryMemConfig, ScopeConfig

# Default: CoreMemory (lightest, zero-config)
Memory = CoreMemory

__version__ = "2.1.0"
__all__ = [
    # Tiered memory classes
    "CoreMemory",
    "SmartMemory",
    "FullMemory",
    "Memory",
    # Simplified interface
    "Dhee",
    "Engram",
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


def _load_teaching():
    """Lazy-load teaching module to avoid import overhead when not needed."""
    from dhee.teaching import ConceptStore, StudentModel, TeachingMemory, TeachingConfig
    return ConceptStore, StudentModel, TeachingMemory, TeachingConfig
