"""engram-enterprise — governance layer for Engram memory.

Provides:
- PersonalMemoryKernel: orchestrates acceptance, provenance, invariants, sleep cycles
- Policy engine: feature flags and scoping
- Episodic store, staging, refcounts
- Async wrappers for memory, SQLite, LLM, embedder
- REST API + CLI
- Claude Code and OpenClaw integrations
"""

__version__ = "0.1.0"

from engram_enterprise.kernel import PersonalMemoryKernel
from engram_enterprise.policy import feature_enabled

__all__ = [
    "PersonalMemoryKernel",
    "feature_enabled",
]
