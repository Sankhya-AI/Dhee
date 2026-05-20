"""Factories for creating embedder, LLM, and vector store instances."""

import logging
import os
from typing import Any, Dict, Optional, Tuple

from dhee.provider_defaults import DEFAULT_PROVIDER, embedding_dims_for

logger = logging.getLogger(__name__)


def _normalize_sqlite_vec_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce directory-style vector paths into sqlite-vec DB files."""
    normalized = dict(config or {})
    path = normalized.get("path")
    if not path:
        return normalized

    path = str(path)
    root, ext = os.path.splitext(path)
    if ext:
        return normalized

    normalized["path"] = os.path.join(path, "sqlite_vec.db")
    return normalized


def _dhee_model_available() -> bool:
    """Check if local DheeModel GGUF is available."""
    try:
        from dhee.llms.dhee import is_dhee_model_available
        return is_dhee_model_available()
    except ImportError:
        return False


def _qwen_embedder_available() -> bool:
    """Check if Qwen3-Embedding can be loaded locally."""
    try:
        import sentence_transformers
        return True
    except ImportError:
        return False


def _detect_provider() -> Tuple[str, str]:
    """Auto-detect the best available LLM/embedder provider.

    Returns (embedder_provider, llm_provider) tuple.

    NVIDIA is the default provider across Dhee. Other providers remain
    available when explicitly configured, but auto-detection prefers NVIDIA.
    """
    try:
        from dhee.cli_config import get_api_key
    except Exception:
        get_api_key = None  # type: ignore[assignment]

    if (
        os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("NVIDIA_QWEN_API_KEY")
        or os.environ.get("NVIDIA_EMBEDDING_API_KEY")
        or os.environ.get("NVIDIA_EMBED_API_KEY")
        or os.environ.get("NVIDIA_LLAMA_4_MAV_API_KEY")
        or (get_api_key and get_api_key("nvidia"))
    ):
        return ("nvidia", "nvidia")
    return (DEFAULT_PROVIDER, DEFAULT_PROVIDER)


class EmbedderFactory:
    @classmethod
    def create(cls, provider: str, config: Dict[str, Any]):
        if provider == "gemini":
            from dhee.embeddings.gemini import GeminiEmbedder

            return GeminiEmbedder(config)
        if provider == "simple":
            from dhee.embeddings.simple import SimpleEmbedder

            return SimpleEmbedder(config)
        if provider == "openai":
            from dhee.embeddings.openai import OpenAIEmbedder

            return OpenAIEmbedder(config)
        if provider == "ollama":
            from dhee.embeddings.ollama import OllamaEmbedder

            return OllamaEmbedder(config)
        if provider == "nvidia":
            from dhee.embeddings.nvidia import NvidiaEmbedder

            return NvidiaEmbedder(config)
        if provider == "qwen":
            from dhee.embeddings.qwen import QwenEmbedder

            return QwenEmbedder(config)
        raise ValueError(f"Unsupported embedder provider: {provider}")

    @classmethod
    def create_auto(cls, config: Optional[Dict[str, Any]] = None):
        """Auto-detect best available embedder. NVIDIA is the default."""
        embedder_provider, _ = _detect_provider()
        cfg = dict(config or {})
        cfg.setdefault("embedding_dims", embedding_dims_for(embedder_provider))
        return cls.create(embedder_provider, cfg)


class LLMFactory:
    @classmethod
    def create(cls, provider: str, config: Dict[str, Any]):
        if provider == "gemini":
            from dhee.llms.gemini import GeminiLLM

            return GeminiLLM(config)
        if provider == "mock":
            from dhee.llms.mock import MockLLM

            return MockLLM(config)
        if provider == "openai":
            from dhee.llms.openai import OpenAILLM

            return OpenAILLM(config)
        if provider == "ollama":
            from dhee.llms.ollama import OllamaLLM

            return OllamaLLM(config)
        if provider == "nvidia":
            from dhee.llms.nvidia import NvidiaLLM

            return NvidiaLLM(config)
        if provider == "dhee":
            from dhee.llms.dhee import DheeLLM

            return DheeLLM(config)
        raise ValueError(f"Unsupported LLM provider: {provider}")

    @classmethod
    def create_auto(cls, config: Optional[Dict[str, Any]] = None):
        """Auto-detect best available LLM. NVIDIA is the default."""
        _, llm_provider = _detect_provider()
        return cls.create(llm_provider, dict(config or {}))


class VectorStoreFactory:
    @classmethod
    def create(cls, provider: str, config: Dict[str, Any]):
        if provider == "memory":
            from dhee.vector_stores.memory import InMemoryVectorStore

            return InMemoryVectorStore(config)
        if provider == "sqlite_vec":
            from dhee.vector_stores.sqlite_vec import SqliteVecStore

            return SqliteVecStore(_normalize_sqlite_vec_config(config))
        if provider == "zvec":
            try:
                from dhee.vector_stores.zvec_store import ZvecStore
                return ZvecStore(config)
            except ImportError:
                logger.warning("zvec not installed, falling back to sqlite_vec")
                try:
                    from dhee.vector_stores.sqlite_vec import SqliteVecStore
                    return SqliteVecStore(
                        _normalize_sqlite_vec_config(config)
                    )
                except ImportError:
                    logger.warning("sqlite_vec not installed, falling back to in-memory")
                    from dhee.vector_stores.memory import InMemoryVectorStore
                    return InMemoryVectorStore(config)
        raise ValueError(f"Unsupported vector store provider: {provider}")
