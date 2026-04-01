"""Simplified Dhee interface for 3-line integration.

This module provides the Dhee class — a batteries-included interface
for the 4-operation cognition API (remember/recall/context/checkpoint).

Usage:
    from dhee import Dhee

    d = Dhee()
    d.remember("User prefers Python over JavaScript")
    d.recall("programming preferences")

Environment Variables:
    OPENAI_API_KEY: OpenAI API key (recommended)
    GEMINI_API_KEY: Google Gemini API key
    DHEE_DATA_DIR: Directory for storage (default: ~/.dhee)
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dhee.checkpoint_runtime import run_checkpoint_common
from dhee.configs.base import (
    CategoryMemConfig,
    EchoMemConfig,
    EmbedderConfig,
    FadeMemConfig,
    LLMConfig,
    MemoryConfig,
    VectorStoreConfig,
)
from dhee.memory.main import FullMemory

logger = logging.getLogger(__name__)


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
    """Get the data directory for Dhee storage."""
    data_dir = os.environ.get("DHEE_DATA_DIR")
    if data_dir:
        return Path(data_dir)
    return Path.home() / ".dhee"


class Engram:
    """Simplified Engram memory interface.

    Provides a clean, minimal API for the full Engram memory system
    with sensible defaults and auto-configuration.

    Example:
        >>> from dhee import Engram
        >>> memory = Engram()
        >>> memory.add("User prefers dark mode", user_id="user123")
        >>> results = memory.search("UI preferences", user_id="user123")

    Args:
        provider: LLM/embedder provider ("gemini" or "openai"). Auto-detected if not set.
        data_dir: Directory for storage. Uses ~/.dhee if not set.
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
        collection_name: str = "dhee",
        in_memory: bool = False,
    ):
        # Auto-detect provider
        self._provider = provider or _detect_provider()
        if in_memory and provider is None and not _has_api_key():
            self._provider = "mock"
        if in_memory and data_dir is None:
            data_dir = tempfile.mkdtemp(prefix="dhee_")
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
            fade=FadeMemConfig(
                enable_forgetting=enable_decay,
            ),
            echo=EchoMemConfig(
                enable_echo=enable_echo,
                auto_depth=True,
            ),
            category=CategoryMemConfig(
                enable_categories=enable_categories,
            ),
            history_db_path=str(self._data_dir / "history.db"),
            collection_name=collection_name,
            embedding_model_dims=embedding_dims,
        )

        self._memory = FullMemory(config)

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
        infer: bool = False,
    ) -> Dict[str, Any]:
        """Add a memory.

        Args:
            content: Memory content (string or list of messages)
            user_id: User identifier (default: "default")
            agent_id: Optional agent identifier
            metadata: Additional metadata to store
            categories: Category tags for organization
            infer: Extract additional facts from content using LLM (default False).
                   Set True only when passing raw conversation turns and you want
                   the LLM to decompose them into atomic facts. Requires a
                   configured LLM provider (OPENAI_API_KEY or GEMINI_API_KEY).

        Returns:
            Dict with results including memory IDs

        Example:
            >>> memory.add("User's favorite color is blue", user_id="u1")
            >>> memory.add([
            ...     {"role": "user", "content": "I prefer Python"},
            ...     {"role": "assistant", "content": "Noted!"}
            ... ], user_id="u1", infer=True)
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
        result = self._memory.get_all(
            user_id=user_id,
            agent_id=agent_id,
            layer=layer,
            limit=limit,
        )
        # Underlying memory.get_all() returns dict {"results": [...]}
        # — normalise to a plain list as documented.
        if isinstance(result, dict):
            return result.get("results", [])
        if isinstance(result, list):
            return result
        return []

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
    def memory(self) -> FullMemory:
        """Expose the configured runtime memory engine for advanced integrations."""
        return self._memory

    @property
    def data_dir(self) -> Path:
        """Data storage directory."""
        return self._data_dir

    def enrich_pending(
        self,
        user_id: str = "default",
        batch_size: int = 10,
        max_batches: int = 5,
    ) -> Dict[str, Any]:
        """Run deferred enrichment using the configured runtime memory engine."""
        return self.memory.enrich_pending(
            user_id=user_id,
            batch_size=batch_size,
            max_batches=max_batches,
        )

    def close(self) -> None:
        """Release runtime resources held by the underlying memory engine."""
        self._memory.close()


class Dhee:
    """4-tool HyperAgent interface — the simplest way to make any agent intelligent.

    The headline API from the README. Wraps the full Engram + Buddhi stack
    behind four methods that mirror the MCP tools exactly.

    Example:
        >>> from dhee import Dhee
        >>> d = Dhee()
        >>> d.remember("User prefers dark mode")
        >>> results = d.recall("what theme does the user like?")
        >>> ctx = d.context("fixing auth bug in login.py")
        >>> d.checkpoint("Fixed auth bug", what_worked="git blame first")

    Args:
        provider: "openai", "gemini", or "ollama". Auto-detected from env.
        data_dir: Storage directory. Defaults to ~/.dhee.
        user_id: Default user ID for all operations. Default "default".
        in_memory: Use in-memory storage (for testing). Default False.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        data_dir: Optional[Union[str, Path]] = None,
        user_id: str = "default",
        in_memory: bool = False,
        auto_context: bool = True,
        auto_checkpoint: bool = True,
        session_timeout: Optional[float] = None,
    ):
        self._user_id = user_id
        self._engram = Engram(
            provider=provider,
            data_dir=data_dir,
            in_memory=in_memory,
        )
        from dhee.core.cognition_kernel import CognitionKernel
        from dhee.core.buddhi import Buddhi
        buddhi_dir = str(self._engram.data_dir / "buddhi")
        self._kernel = CognitionKernel(data_dir=buddhi_dir)
        self._buddhi = Buddhi(data_dir=buddhi_dir, kernel=self._kernel)

        # Passive session tracker — auto-context + auto-checkpoint
        from dhee.core.session_tracker import SessionTracker
        self._tracker = SessionTracker(
            session_timeout=session_timeout,
            auto_context=auto_context,
            auto_checkpoint=auto_checkpoint,
        )

    @property
    def kernel(self):
        """Access the CognitionKernel for direct state manipulation."""
        return self._kernel

    # ------------------------------------------------------------------
    # Tool 1: remember
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store a fact, preference, or observation.

        0 LLM calls on hot path. 1 embedding call. Echo enrichment
        (paraphrases + keywords for better recall) runs at checkpoint.

        Args:
            content: The fact or preference to remember.
            user_id: Override default user_id.
            metadata: Additional metadata to attach.

        Returns:
            {"stored": True, "id": "<memory_id>"}
        """
        uid = user_id or self._user_id

        # Auto-tier memory content (shruti/smriti)
        from dhee.core.session_tracker import classify_tier
        tier = classify_tier(content)
        meta = dict(metadata) if metadata else {}
        if tier != "smriti":
            meta["tier"] = tier

        result = self._engram.add(content, user_id=uid, infer=False, metadata=meta or None)
        response: Dict[str, Any] = {"stored": True}
        memory_id = None
        if isinstance(result, dict):
            rs = result.get("results", [])
            if rs:
                memory_id = rs[0].get("id")
                response["id"] = memory_id
        if tier == "shruti":
            response["tier"] = "shruti"

        # Session tracking — may trigger auto-context
        signals = self._tracker.on_remember(content, memory_id)
        self._handle_tracker_signals(signals, uid)

        # Detect intentions in the content
        intention = self._buddhi.on_memory_stored(content=content, user_id=uid)
        if intention:
            response["detected_intention"] = intention.to_dict()
        return response

    # ------------------------------------------------------------------
    # Tool 2: recall
    # ------------------------------------------------------------------

    def recall(
        self,
        query: str,
        user_id: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search memory for relevant facts.

        0 LLM calls. 1 embedding call. Returns top-K results by relevance.

        Args:
            query: What you're trying to remember.
            user_id: Override default user_id.
            limit: Max results (default 5).

        Returns:
            List of {"memory": str, "score": float, "id": str}
        """
        uid = user_id or self._user_id
        results = self._engram.search(query, user_id=uid, limit=limit)
        formatted = [
            {
                "memory": r.get("memory", r.get("content", "")),
                "score": round(r.get("composite_score", r.get("score", 0.0)), 3),
                "id": r.get("id", ""),
            }
            for r in results
        ]

        # Session tracking — may trigger auto-context
        signals = self._tracker.on_recall(query, formatted)
        self._handle_tracker_signals(signals, uid)

        return formatted

    # ------------------------------------------------------------------
    # Tool 3: context
    # ------------------------------------------------------------------

    def context(
        self,
        task_description: Optional[str] = None,
        user_id: Optional[str] = None,
        operational: bool = False,
    ) -> Dict[str, Any]:
        """HyperAgent session bootstrap. Call once at conversation start.

        Returns everything the agent needs: last session state, performance
        trends, synthesized insights, triggered intentions, warnings, and
        top relevant memories.

        Args:
            task_description: What you're about to work on.
            user_id: Override default user_id.
            operational: If True, return compact actionable-only format
                for per-turn consumption instead of full context.

        Returns:
            HyperContext dict (full or operational).
        """
        uid = user_id or self._user_id
        self._tracker.on_context(task_description)
        hyper_ctx = self._buddhi.get_hyper_context(
            user_id=uid,
            task_description=task_description,
            memory=self._engram.memory,
        )
        if operational:
            return hyper_ctx.to_operational_dict()
        return hyper_ctx.to_dict()

    # ------------------------------------------------------------------
    # Tool 4: checkpoint
    # ------------------------------------------------------------------

    def checkpoint(
        self,
        summary: str,
        task_type: Optional[str] = None,
        outcome_score: Optional[float] = None,
        what_worked: Optional[str] = None,
        what_failed: Optional[str] = None,
        key_decision: Optional[str] = None,
        remember_to: Optional[str] = None,
        trigger_keywords: Optional[List[str]] = None,
        status: str = "paused",
        decisions: Optional[List[str]] = None,
        todos: Optional[List[str]] = None,
        files_touched: Optional[List[str]] = None,
        repo: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: str = "dhee",
    ) -> Dict[str, Any]:
        """Save session state before ending. Where the cognition happens.

        1. Session digest saved for cross-agent handoff.
        2. Batch enrichment of stored memories (1 LLM call per ~10 mems).
        3. Outcome recording → performance tracking.
        4. Insight synthesis: what_worked/failed → transferable learnings.
        5. Intention storage → prospective memory.

        Args:
            summary: What you were working on.
            task_type: Task category (e.g. "bug_fix", "code_review").
            outcome_score: 0.0–1.0 score for performance tracking.
            what_worked: Approach that worked → stored as strategy insight.
            what_failed: Approach that failed → stored as warning insight.
            key_decision: Key decision and rationale.
            remember_to: Future intention ("remember to X when Y").
            trigger_keywords: Keywords that fire the intention.
            status: "active", "paused", or "completed".
            decisions: Key decisions made (for handoff).
            todos: Remaining work items (for handoff).
            files_touched: Files modified (for handoff).
            repo: Repository path.
            user_id: Override default user_id.
            agent_id: Agent identifier.

        Returns:
            Dict with session_saved, memories_enriched, outcome_recorded,
            insights_created, intention_stored.
        """
        uid = user_id or self._user_id
        self._tracker.on_checkpoint()

        # Auto-fill task_type if not provided
        if not task_type:
            task_type = self._tracker.get_inferred_task_type()
            if task_type == "general":
                task_type = None  # don't store noise

        # Auto-fill outcome if not provided and we have enough signals
        if outcome_score is None and self._tracker.op_count >= 3:
            outcome = self._tracker.get_outcome_signals()
            outcome_score = outcome.get("outcome_score")
            if not what_worked:
                what_worked = outcome.get("what_worked")

        return run_checkpoint_common(
            logger=logger,
            log_prefix="Checkpoint",
            user_id=uid,
            summary=summary,
            status=status,
            agent_id=agent_id,
            repo=repo,
            decisions=decisions,
            files_touched=files_touched,
            todos=todos,
            task_type=task_type,
            outcome_score=outcome_score,
            what_worked=what_worked,
            what_failed=what_failed,
            key_decision=key_decision,
            remember_to=remember_to,
            trigger_keywords=trigger_keywords,
            enrich_pending_fn=self._engram.enrich_pending,
            record_outcome_fn=self._buddhi.record_outcome,
            reflect_fn=self._buddhi.reflect,
            store_intention_fn=self._buddhi.store_intention,
        )

    # ------------------------------------------------------------------
    # Auto-lifecycle (driven by SessionTracker)
    # ------------------------------------------------------------------

    def _handle_tracker_signals(self, signals: Dict[str, Any], user_id: str) -> None:
        """Process signals from the session tracker."""
        if not signals:
            return

        # Auto-checkpoint a timed-out previous session
        if signals.get("needs_auto_checkpoint"):
            args = signals.get("auto_checkpoint_args", {})
            try:
                checkpoint_result = self.checkpoint(user_id=user_id, **args)
                for warning in checkpoint_result.get("warnings", []):
                    logger.warning("Auto-checkpoint warning: %s", warning)
            except Exception as exc:
                logger.warning("Auto-checkpoint failed: %s", exc, exc_info=True)

        # Auto-context for new session
        if signals.get("needs_auto_context"):
            task = signals.get("inferred_task")
            try:
                self.context(task_description=task, user_id=user_id)
            except Exception as exc:
                logger.warning("Auto-context failed: %s", exc, exc_info=True)

    def close(self) -> None:
        """Flush cognition state and release runtime resources."""
        errors: List[str] = []

        try:
            self._buddhi.flush()
        except Exception as exc:
            logger.exception("Dhee close failed for buddhi.flush")
            errors.append(f"buddhi.flush: {type(exc).__name__}: {exc}")

        try:
            self._engram.close()
        except Exception as exc:
            logger.exception("Dhee close failed for engram.close")
            errors.append(f"engram.close: {type(exc).__name__}: {exc}")

        if errors:
            raise RuntimeError(
                "Failed to close Dhee resources: " + "; ".join(errors)
            )
