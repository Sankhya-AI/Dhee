"""engram-resilience — Graceful degradation and fault tolerance for AI agents.

Model fallback chains, smart retry with backoff, and context compaction
for long conversations.

Usage::

    from engram_resilience import FallbackChain, SmartRetry, ContextCompactor

    chain = FallbackChain([
        {"provider": "gemini", "model": "gemini-2.0-flash"},
        {"provider": "openai", "model": "gpt-4o-mini"},
    ])
    result = chain.generate("Hello")
"""

from engram_resilience.config import ResilienceConfig
from engram_resilience.fallback import FallbackChain
from engram_resilience.retry import SmartRetry
from engram_resilience.compaction import ContextCompactor

__all__ = ["FallbackChain", "SmartRetry", "ContextCompactor", "ResilienceConfig"]
