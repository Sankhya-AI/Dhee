"""Simplified Engram interface for 3-line integration.

This module provides the Engram class - a simplified, batteries-included
interface for the Engram memory layer.

Usage:
    from engram import Engram

    memory = Engram()  # Auto-configures based on environment
    memory.add("User prefers Python over JavaScript", user_id="u123")
    results = memory.search("programming preferences", user_id="u123")

Environment Variables:
    GEMINI_API_KEY: Google Gemini API key (preferred)
    OPENAI_API_KEY: OpenAI API key (fallback)
    ENGRAM_DATA_DIR: Directory for storage (default: ~/.engram)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from engram.configs.base import (
    CategoryMemConfig,
    EchoMemConfig,
    EmbedderConfig,
    FadeMemConfig,
    LLMConfig,
    MemoryConfig,
    VectorStoreConfig,
)
from engram.memory.main import Memory


def _detect_provider() -> str:
    """Detect which LLM/embedder provider to use based on environment."""
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    # Default to gemini if no key found (will fail later with clear error)
    return "gemini"


def _get_embedding_dims(provider: str) -> int:
    """Get embedding dimensions for provider."""
    return 3072 if provider == "gemini" else 1536


def _has_api_key() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def _get_data_dir() -> Path:
    """Get the data directory for Engram storage."""
    data_dir = os.environ.get("ENGRAM_DATA_DIR")
    if data_dir:
        return Path(data_dir)
    return Path.home() / ".engram"


class Engram:
    """Simplified Engram memory interface.

    Provides a clean, minimal API for the full Engram memory system
    with sensible defaults and auto-configuration.

    Example:
        >>> from engram import Engram
        >>> memory = Engram()
        >>> memory.add("User prefers dark mode", user_id="user123")
        >>> results = memory.search("UI preferences", user_id="user123")

    Args:
        provider: LLM/embedder provider ("gemini" or "openai"). Auto-detected if not set.
        data_dir: Directory for storage. Uses ~/.engram if not set.
        enable_echo: Enable EchoMem multi-modal encoding. Default True.
        enable_categories: Enable CategoryMem organization. Default True.
        enable_decay: Enable FadeMem forgetting. Default True.
        collection_name: Vector store collection name. Default "engram".
        in_memory: Use in-memory storage (for testing). Default False.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        data_dir: Optional[Union[str, Path]] = None,
        enable_echo: bool = True,
        enable_categories: bool = True,
        enable_decay: bool = True,
        collection_name: str = "engram",
        in_memory: bool = False,
    ):
        # Auto-detect provider
        self._provider = provider or _detect_provider()
        if in_memory and provider is None and not _has_api_key():
            self._provider = "mock"
        if in_memory and data_dir is None:
            data_dir = tempfile.mkdtemp(prefix="engram_")
        self._data_dir = Path(data_dir) if data_dir else _get_data_dir()
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Build configuration
        embedding_dims = _get_embedding_dims(self._provider)

        if in_memory:
            vector_config = VectorStoreConfig(
                provider="memory",
                config={
                    "collection_name": collection_name,
                    "embedding_model_dims": embedding_dims,
                },
            )
        else:
            vector_config = VectorStoreConfig(
                provider="sqlite_vec",
                config={
                    "collection_name": collection_name,
                    "path": str(self._data_dir / "sqlite_vec.db"),
                    "embedding_model_dims": embedding_dims,
                },
            )

        llm_provider = "mock" if self._provider == "mock" else self._provider
        embedder_provider = "simple" if self._provider == "mock" else self._provider
        embedder_kwargs: Dict[str, Any] = {}
        if embedder_provider == "simple":
            embedder_kwargs["config"] = {"embedding_dims": embedding_dims}

        config = MemoryConfig(
            llm=LLMConfig(provider=llm_provider),
            embedder=EmbedderConfig(provider=embedder_provider, **embedder_kwargs),
            vector_store=vector_config,
            engram=FadeMemConfig(
                enable_forgetting=enable_decay,
            ),
            echo=EchoMemConfig(
                enable_echo=enable_echo,
                auto_depth=True,
            ),
            category=CategoryMemConfig(
                enable_categories=enable_categories,
            ),
            history_db_path=str(self._data_dir / "engram.db"),
            collection_name=collection_name,
            embedding_model_dims=embedding_dims,
        )

        self._memory = Memory(config)

    def add(
        self,
        content: Union[str, List[Dict[str, str]]],
        user_id: str = "default",
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        categories: Optional[List[str]] = None,
        agent_category: Optional[str] = None,
        connector_id: Optional[str] = None,
        scope: Optional[str] = None,
        source_app: Optional[str] = None,
        infer: bool = True,
    ) -> Dict[str, Any]:
        """Add a memory.

        Args:
            content: Memory content (string or list of messages)
            user_id: User identifier (default: "default")
            agent_id: Optional agent identifier
            metadata: Additional metadata to store
            categories: Category tags for organization
            infer: Extract facts from content (default True)

        Returns:
            Dict with results including memory IDs

        Example:
            >>> memory.add("User's favorite color is blue", user_id="u1")
            >>> memory.add([
            ...     {"role": "user", "content": "I prefer Python"},
            ...     {"role": "assistant", "content": "Noted!"}
            ... ], user_id="u1")
        """
        return self._memory.add(
            messages=content,
            user_id=user_id,
            agent_id=agent_id,
            metadata=metadata,
            categories=categories,
            agent_category=agent_category,
            connector_id=connector_id,
            scope=scope,
            source_app=source_app,
            infer=infer,
        )

    def search(
        self,
        query: str,
        user_id: str = "default",
        agent_id: Optional[str] = None,
        limit: int = 10,
        categories: Optional[List[str]] = None,
        agent_category: Optional[str] = None,
        connector_ids: Optional[List[str]] = None,
        scope_filter: Optional[Union[str, List[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """Search memories.

        Args:
            query: Search query (semantic search)
            user_id: User identifier
            agent_id: Optional agent identifier
            limit: Maximum results to return
            categories: Filter by category

        Returns:
            List of matching memories with scores

        Example:
            >>> results = memory.search("color preferences", user_id="u1")
            >>> for r in results:
            ...     print(r["content"], r["score"])
        """
        result = self._memory.search(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
            categories=categories,
            agent_category=agent_category,
            connector_ids=connector_ids,
            scope_filter=scope_filter,
        )
        if isinstance(result, dict) and "results" in result:
            results = result["results"]
        elif isinstance(result, list):
            results = result
        else:
            return []
        for entry in results:
            if isinstance(entry, dict) and "content" not in entry and "memory" in entry:
                entry["content"] = entry.get("memory")
        return results

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID.

        Args:
            memory_id: The memory's unique identifier

        Returns:
            Memory dict or None if not found
        """
        return self._memory.get(memory_id)

    def get_all(
        self,
        user_id: str = "default",
        agent_id: Optional[str] = None,
        layer: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get all memories for a user.

        Args:
            user_id: User identifier
            agent_id: Optional agent identifier
            layer: Filter by layer ("sml" or "lml")
            limit: Maximum results

        Returns:
            List of memories
        """
        return self._memory.get_all(
            user_id=user_id,
            agent_id=agent_id,
            layer=layer,
            limit=limit,
        )

    def update(self, memory_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a memory.

        Args:
            memory_id: Memory to update
            data: Fields to update (content, metadata)

        Returns:
            Updated memory
        """
        return self._memory.update(memory_id, data)

    def delete(self, memory_id: str) -> None:
        """Delete a memory.

        Args:
            memory_id: Memory to delete
        """
        self._memory.delete(memory_id)

    def forget(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Apply memory decay (forgetting).

        Weakens memories based on time and access patterns.
        Memories below strength threshold are forgotten.
        High-access memories may be promoted to long-term.

        Args:
            user_id: Scope to user (optional)
            agent_id: Scope to agent (optional)

        Returns:
            Dict with decayed, forgotten, promoted counts
        """
        scope = {}
        if user_id:
            scope["user_id"] = user_id
        if agent_id:
            scope["agent_id"] = agent_id
        return self._memory.apply_decay(scope=scope if scope else None)

    def stats(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get memory statistics.

        Args:
            user_id: Filter by user
            agent_id: Filter by agent

        Returns:
            Dict with total, sml_count, lml_count, categories
        """
        return self._memory.get_stats(user_id=user_id, agent_id=agent_id)

    def categories(self) -> List[Dict[str, Any]]:
        """Get all categories.

        Returns:
            List of category definitions
        """
        return self._memory.get_categories()

    @property
    def provider(self) -> str:
        """Current LLM/embedder provider."""
        return self._provider

    @property
    def data_dir(self) -> Path:
        """Data storage directory."""
        return self._data_dir
