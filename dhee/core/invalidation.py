"""Dhee v3 — Three-Tier Invalidation Engine.

Graduated invalidation based on what happened to the source:

1. Hard invalidation: source deleted → derived tombstoned
2. Soft invalidation: source corrected → derived marked stale, re-eval queued
3. Partial invalidation: one of N sources changed, contribution < 30%
   → derived marked suspect with confidence penalty

Design contract:
    - Invalidation is async — marks status + enqueues repair jobs
    - Never synchronously rewrites derived objects
    - Type-aware: each derived type has its own invalidation response
    - All cascades are traceable via maintenance_jobs
    - Zero LLM calls
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Threshold: if a changed source contributed >= this fraction,
# escalate from partial to soft invalidation
PARTIAL_ESCALATION_THRESHOLD = 0.30


class InvalidationEngine:
    """Cascades invalidation from raw events to derived objects.

    Usage:
        engine = InvalidationEngine(lineage, stores_map, job_enqueuer)
        engine.on_event_corrected(event_id)  # soft + partial
        engine.on_event_deleted(event_id)    # hard + partial
    """

    def __init__(
        self,
        lineage: "DerivedLineageStore",
        stores: Dict[str, Any],
        conn: "sqlite3.Connection",
        lock: "threading.RLock",
    ):
        """
        Args:
            lineage: DerivedLineageStore for tracing dependencies
            stores: Map of derived_type → store instance, e.g.
                    {"belief": belief_store, "policy": policy_store, ...}
            conn: Shared SQLite connection for enqueuing jobs
            lock: Shared threading lock
        """
        self.lineage = lineage
        self.stores = stores
        self._conn = conn
        self._lock = lock

    def on_event_corrected(self, event_id: str) -> Dict[str, Any]:
        """Handle a raw event being corrected (superseded by new event).

        For each dependent derived object:
        - If sole source → soft invalidation (stale)
        - If one of many → check contribution weight:
            - >= 30% → soft invalidation
            - < 30% → partial invalidation (suspect + confidence penalty)
        """
        return self._cascade(event_id, mode="corrected")

    def on_event_deleted(self, event_id: str) -> Dict[str, Any]:
        """Handle a raw event being deleted.

        For each dependent derived object:
        - If sole source → hard invalidation (tombstone)
        - If one of many → check contribution weight:
            - >= 30% → soft invalidation (stale + re-eval)
            - < 30% → partial invalidation (suspect + confidence penalty)
        """
        return self._cascade(event_id, mode="deleted")

    def _cascade(self, event_id: str, mode: str) -> Dict[str, Any]:
        """Core cascade logic for both correction and deletion."""
        dependents = self.lineage.get_dependents(event_id)

        result = {
            "event_id": event_id,
            "mode": mode,
            "hard_invalidated": [],
            "soft_invalidated": [],
            "partial_invalidated": [],
            "jobs_enqueued": [],
            "errors": [],
        }

        for dep in dependents:
            dtype = dep["derived_type"]
            did = dep["derived_id"]
            weight = dep["contribution_weight"]

            try:
                # How many total sources does this derived object have?
                total_sources = self.lineage.get_source_count(dtype, did)

                if total_sources <= 1:
                    # Sole source — hard or soft depending on mode
                    if mode == "deleted":
                        self._hard_invalidate(dtype, did)
                        result["hard_invalidated"].append(
                            {"type": dtype, "id": did}
                        )
                    else:  # corrected
                        self._soft_invalidate(dtype, did)
                        job_id = self._enqueue_repair(
                            dtype, did, "repair_stale_derived"
                        )
                        result["soft_invalidated"].append(
                            {"type": dtype, "id": did}
                        )
                        result["jobs_enqueued"].append(job_id)

                elif weight >= PARTIAL_ESCALATION_THRESHOLD:
                    # Major contributor — treat as soft invalidation
                    self._soft_invalidate(dtype, did)
                    job_id = self._enqueue_repair(
                        dtype, did, "repair_stale_derived"
                    )
                    result["soft_invalidated"].append(
                        {"type": dtype, "id": did, "weight": weight}
                    )
                    result["jobs_enqueued"].append(job_id)

                else:
                    # Minor contributor — partial invalidation
                    self._partial_invalidate(dtype, did, weight)
                    job_id = self._enqueue_repair(
                        dtype, did, "verify_suspect_derived"
                    )
                    result["partial_invalidated"].append(
                        {"type": dtype, "id": did, "weight": weight}
                    )
                    result["jobs_enqueued"].append(job_id)

            except Exception as e:
                logger.exception(
                    "Invalidation failed for %s:%s from event %s",
                    dtype, did, event_id,
                )
                result["errors"].append({
                    "type": dtype, "id": did, "error": str(e)
                })

        return result

    # ------------------------------------------------------------------
    # Invalidation tier implementations
    # ------------------------------------------------------------------

    def _hard_invalidate(self, dtype: str, did: str) -> None:
        """Source gone, child unusable. Mark as tombstone."""
        store = self.stores.get(dtype)
        if store and hasattr(store, "set_status"):
            store.set_status(did, "invalidated")
            logger.info("Hard invalidated %s:%s", dtype, did)

    def _soft_invalidate(self, dtype: str, did: str) -> None:
        """Source changed, child needs re-evaluation."""
        store = self.stores.get(dtype)
        if store and hasattr(store, "set_status"):
            store.set_status(did, "stale")
            logger.info("Soft invalidated %s:%s → stale", dtype, did)

    def _partial_invalidate(
        self, dtype: str, did: str, weight: float
    ) -> None:
        """Minor source change. Mark suspect + confidence penalty."""
        store = self.stores.get(dtype)
        if not store:
            return

        # Apply confidence penalty proportional to contribution weight
        if hasattr(store, "get") and hasattr(store, "update_confidence"):
            obj = store.get(did)
            if obj and "confidence" in obj:
                penalty = weight * 0.5  # half the contribution weight
                new_conf = max(0.05, obj["confidence"] - penalty)
                store.update_confidence(
                    did, new_conf,
                    new_status="suspect",
                    revision_reason=f"partial_invalidation (weight={weight:.2f})",
                )
        elif hasattr(store, "set_status"):
            store.set_status(did, "suspect")

        logger.info(
            "Partial invalidated %s:%s → suspect (weight=%.2f)",
            dtype, did, weight,
        )

    # ------------------------------------------------------------------
    # Job enqueuing
    # ------------------------------------------------------------------

    def _enqueue_repair(
        self, dtype: str, did: str, job_name: str
    ) -> str:
        """Enqueue a repair job for a derived object."""
        job_id = str(uuid.uuid4())
        now = _utcnow_iso()
        payload = json.dumps({
            "derived_type": dtype,
            "derived_id": did,
        })
        idem_key = f"{job_name}:{dtype}:{did}"

        with self._lock:
            try:
                # Check idempotency — don't enqueue if already pending/running
                existing = self._conn.execute(
                    """SELECT job_id FROM maintenance_jobs
                       WHERE idempotency_key = ?
                       AND status IN ('pending', 'running')
                       LIMIT 1""",
                    (idem_key,),
                ).fetchone()

                if existing:
                    return existing["job_id"]

                self._conn.execute(
                    """INSERT INTO maintenance_jobs
                       (job_id, job_name, status, payload_json,
                        created_at, idempotency_key)
                       VALUES (?, ?, 'pending', ?, ?, ?)""",
                    (job_id, job_name, payload, now, idem_key),
                )
                self._conn.commit()
                return job_id
            except Exception:
                self._conn.rollback()
                raise
