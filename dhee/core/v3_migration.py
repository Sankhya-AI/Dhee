"""Dhee v3 — Migration Bridge: dual-write from v2 to v3.

Phase 10 migration strategy:
1. Add raw event store without changing external API
2. Dual-write: old path + new raw event path
3. Backfill old engrams into raw + derived form

This module provides the dual-write bridge and backfill utilities.
The external API (remember/recall/context/checkpoint) stays stable.

Design contract:
    - Old path continues to work — no breakage
    - New path writes to v3 raw events in parallel
    - Feature flag controls whether recall reads from v3
    - Backfill is idempotent and resumable
    - Zero LLM calls
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Feature flags (env vars)
def _flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


class V3MigrationBridge:
    """Dual-write bridge between v2 (UniversalEngram) and v3 (event-sourced).

    Usage:
        bridge = V3MigrationBridge(v3_store)
        # In remember():
        bridge.on_remember(content, user_id, memory_id)
        # In recall():
        if bridge.should_use_v3_read():
            results = bridge.recall_from_v3(query, user_id)

    Feature flags:
        DHEE_V3_WRITE=1  → dual-write to v3 raw events (default: on)
        DHEE_V3_READ=1   → read from v3 retrieval view (default: off)
    """

    def __init__(
        self,
        v3_store: Optional["CognitionStore"] = None,
    ):
        self._store = v3_store
        self._write_enabled = _flag("DHEE_V3_WRITE", default=True)
        self._read_enabled = _flag("DHEE_V3_READ", default=False)

    @property
    def write_enabled(self) -> bool:
        return self._write_enabled and self._store is not None

    @property
    def read_enabled(self) -> bool:
        return self._read_enabled and self._store is not None

    # ------------------------------------------------------------------
    # Dual-write hooks
    # ------------------------------------------------------------------

    def on_remember(
        self,
        content: str,
        user_id: str,
        *,
        session_id: Optional[str] = None,
        source: str = "user",
        metadata: Optional[Dict[str, Any]] = None,
        v2_memory_id: Optional[str] = None,
    ) -> Optional[str]:
        """Dual-write: store raw event alongside v2 memory.

        Returns the v3 event_id, or None if v3 write is disabled.
        """
        if not self.write_enabled:
            return None

        try:
            meta = metadata or {}
            if v2_memory_id:
                meta["v2_memory_id"] = v2_memory_id

            event = self._store.events.add(
                content=content,
                user_id=user_id,
                session_id=session_id,
                source=source,
                metadata=meta,
            )
            return event.event_id
        except Exception as e:
            logger.warning("v3 dual-write failed (non-fatal): %s", e)
            return None

    def on_correction(
        self,
        original_content: str,
        new_content: str,
        user_id: str,
    ) -> Optional[str]:
        """Handle a memory correction in v3.

        Finds the original event by content hash and creates a correction.
        """
        if not self.write_enabled:
            return None

        try:
            content_hash = hashlib.sha256(
                original_content.encode("utf-8")
            ).hexdigest()
            original = self._store.events.get_by_hash(content_hash, user_id)
            if not original:
                # Original not in v3 yet — just add the new content
                event = self._store.events.add(
                    content=new_content, user_id=user_id,
                    source="user_correction",
                )
                return event.event_id

            correction = self._store.events.correct(
                original.event_id, new_content,
                source="user_correction",
            )
            return correction.event_id
        except Exception as e:
            logger.warning("v3 correction failed (non-fatal): %s", e)
            return None

    # ------------------------------------------------------------------
    # Backfill: v2 engrams → v3 raw events
    # ------------------------------------------------------------------

    def backfill_from_v2(
        self,
        memories: List[Dict[str, Any]],
        *,
        user_id: str = "default",
        batch_size: int = 100,
    ) -> Dict[str, int]:
        """Backfill v2 memories into v3 raw events.

        Idempotent: content-hash dedup prevents duplicates.

        Args:
            memories: List of v2 memory dicts with at least 'memory' key
            user_id: Default user ID if not in memory dict
            batch_size: Process in batches for progress reporting

        Returns:
            {"total": N, "created": M, "skipped_dedup": K, "errors": E}
        """
        if not self._store:
            return {"total": 0, "error": "v3 store not initialized"}

        stats = {"total": len(memories), "created": 0, "skipped_dedup": 0, "errors": 0}

        for i, mem in enumerate(memories):
            content = mem.get("memory", mem.get("content", ""))
            if not content:
                stats["errors"] += 1
                continue

            uid = mem.get("user_id", user_id)
            created_at = mem.get("created_at")

            try:
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                existing = self._store.events.get_by_hash(content_hash, uid)
                if existing:
                    stats["skipped_dedup"] += 1
                    continue

                meta = {}
                v2_id = mem.get("id", mem.get("memory_id"))
                if v2_id:
                    meta["v2_memory_id"] = v2_id
                if mem.get("layer"):
                    meta["v2_layer"] = mem["layer"]
                if mem.get("strength"):
                    meta["v2_strength"] = mem["strength"]

                self._store.events.add(
                    content=content,
                    user_id=uid,
                    source="v2_backfill",
                    metadata=meta,
                )
                stats["created"] += 1

            except Exception as e:
                logger.warning("Backfill error for memory %d: %s", i, e)
                stats["errors"] += 1

        return stats

    # ------------------------------------------------------------------
    # v3 read path (behind feature flag)
    # ------------------------------------------------------------------

    def should_use_v3_read(self) -> bool:
        return self.read_enabled

    def get_v3_stats(self) -> Dict[str, Any]:
        """Get basic stats about v3 state (for monitoring)."""
        if not self._store:
            return {"v3_available": False}

        try:
            return {
                "v3_available": True,
                "write_enabled": self._write_enabled,
                "read_enabled": self._read_enabled,
                "event_count": self._store.events.count("default"),
            }
        except Exception as e:
            return {"v3_available": True, "error": str(e)}
