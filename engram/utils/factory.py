"""Factories for creating embedder, LLM, and vector store instances."""

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _detect_provider() -> Tuple[str, str]:
    """Auto-detect the best available LLM/embedder provider.

    Returns (embedder_provider, llm_provider) tuple.

    Detection order:
    1. GEMINI_API_KEY / GOOGLE_API_KEY set → gemini
    2. OPENAI_API_KEY set → openai
    3. Ollama running on localhost:11434 → ollama
    4. Fall back to simple embedder + mock LLM (zero-config, no API key)
    """
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return ("gemini", "gemini")
    if os.environ.get("OPENAI_API_KEY"):
        return ("openai", "openai")

    # Try Ollama
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=1)
        if resp.status_code == 200:
            return ("ollama", "ollama")
    except Exception:
        pass

    # Zero-config fallback: hash embedder + mock LLM
    return ("simple", "mock")


class EmbedderFactory:
    @classmethod
    def create(cls, provider: str, config: Dict[str, Any]):
        if provider == "gemini":
            from engram.embeddings.gemini import GeminiEmbedder

            return GeminiEmbedder(config)
        if provider == "simple":
            from engram.embeddings.simple import SimpleEmbedder

            return SimpleEmbedder(config)
        if provider == "openai":
            from engram.embeddings.openai import OpenAIEmbedder

            return OpenAIEmbedder(config)
        if provider == "ollama":
            from engram.embeddings.ollama import OllamaEmbedder

            return OllamaEmbedder(config)
        if provider == "nvidia":
            from engram.embeddings.nvidia import NvidiaEmbedder

            return NvidiaEmbedder(config)
        raise ValueError(f"Unsupported embedder provider: {provider}")

    @classmethod
    def create_auto(cls, config: Optional[Dict[str, Any]] = None):
        """Auto-detect best available embedder. No API key required."""
        embedder_provider, _ = _detect_provider()
        cfg = dict(config or {})
        if embedder_provider == "simple":
            cfg.setdefault("embedding_dims", 384)
        return cls.create(embedder_provider, cfg)


class LLMFactory:
    @classmethod
    def create(cls, provider: str, config: Dict[str, Any]):
        if provider == "gemini":
            from engram.llms.gemini import GeminiLLM

            return GeminiLLM(config)
        if provider == "mock":
            from engram.llms.mock import MockLLM

            return MockLLM(config)
        if provider == "openai":
            from engram.llms.openai import OpenAILLM

            return OpenAILLM(config)
        if provider == "ollama":
            from engram.llms.ollama import OllamaLLM

            return OllamaLLM(config)
        if provider == "nvidia":
            from engram.llms.nvidia import NvidiaLLM

            return NvidiaLLM(config)
        raise ValueError(f"Unsupported LLM provider: {provider}")

    @classmethod
    def create_auto(cls, config: Optional[Dict[str, Any]] = None):
        """Auto-detect best available LLM. Falls back to mock."""
        _, llm_provider = _detect_provider()
        return cls.create(llm_provider, dict(config or {}))


class VectorStoreFactory:
    @classmethod
    def create(cls, provider: str, config: Dict[str, Any]):
        if provider == "memory":
            from engram.vector_stores.memory import InMemoryVectorStore

            return InMemoryVectorStore(config)
        if provider == "sqlite_vec":
            from engram.vector_stores.sqlite_vec import SqliteVecStore

            return SqliteVecStore(config)
        if provider == "zvec":
            try:
                from engram.vector_stores.zvec_store import ZvecStore
                return ZvecStore(config)
            except ImportError:
                logger.warning("zvec not installed, falling back to sqlite_vec")
                try:
                    from engram.vector_stores.sqlite_vec import SqliteVecStore
                    return SqliteVecStore(config)
                except ImportError:
                    logger.warning("sqlite_vec not installed, falling back to in-memory")
                    from engram.vector_stores.memory import InMemoryVectorStore
                    return InMemoryVectorStore(config)
        raise ValueError(f"Unsupported vector store provider: {provider}")
