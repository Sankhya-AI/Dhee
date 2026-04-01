"""Dhee v3 — Cognitive Conflict Store + Auto-Resolution.

Tracks contradictions and disagreements between derived objects.
Conflicts are explicit rows, not silent resolution.

Auto-resolution: if one side has confidence > 0.8 and the other < 0.3,
auto-resolve in favor of the high-confidence side. Otherwise, flag
for manual resolution.

Conflict types:
    - belief_contradiction: two beliefs claim opposing things
    - anchor_disagreement: anchor candidate disagrees with resolved anchor
    - distillation_conflict: candidate conflicts with promoted truth
    - invalidation_dispute: partial invalidation verification disagrees

Design contract:
    - Every contradiction gets an explicit row
    - Auto-resolution only when confidence gap > 0.5
    - Zero LLM calls — confidence comparison only
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Auto-resolution thresholds
AUTO_RESOLVE_HIGH = 0.8
AUTO_RESOLVE_LOW = 0.3
AUTO_RESOLVE_GAP = 0.5


class ConflictStore:
    """Manages cognitive conflicts in the database."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def create(
        self,
        conflict_type: str,
        side_a_type: str,
        side_a_id: str,
        side_b_type: str,
        side_b_id: str,
        *,
        side_a_confidence: Optional[float] = None,
        side_b_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Create a conflict. Attempts auto-resolution if confidence gap is clear.

        Returns dict with conflict_id, resolution_status, and auto_resolution details.
        """
        cid = str(uuid.uuid4())
        now = _utcnow_iso()

        # Attempt auto-resolution
        resolution_status = "open"
        resolution_json = None
        auto_confidence = None

        if side_a_confidence is not None and side_b_confidence is not None:
            gap = abs(side_a_confidence - side_b_confidence)
            if gap >= AUTO_RESOLVE_GAP:
                if (side_a_confidence >= AUTO_RESOLVE_HIGH
                        and side_b_confidence <= AUTO_RESOLVE_LOW):
                    resolution_status = "auto_resolved"
                    auto_confidence = side_a_confidence
                    resolution_json = json.dumps({
                        "winner": "side_a",
                        "winner_type": side_a_type,
                        "winner_id": side_a_id,
                        "reason": f"confidence gap: {side_a_confidence:.2f} vs {side_b_confidence:.2f}",
                    })
                elif (side_b_confidence >= AUTO_RESOLVE_HIGH
                      and side_a_confidence <= AUTO_RESOLVE_LOW):
                    resolution_status = "auto_resolved"
                    auto_confidence = side_b_confidence
                    resolution_json = json.dumps({
                        "winner": "side_b",
                        "winner_type": side_b_type,
                        "winner_id": side_b_id,
                        "reason": f"confidence gap: {side_b_confidence:.2f} vs {side_a_confidence:.2f}",
                    })

        with self._tx() as conn:
            conn.execute(
                """INSERT INTO cognitive_conflicts
                   (conflict_id, conflict_type, side_a_type, side_a_id,
                    side_b_type, side_b_id, detected_at,
                    resolution_status, resolution_json,
                    auto_resolution_confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid, conflict_type, side_a_type, side_a_id,
                    side_b_type, side_b_id, now,
                    resolution_status, resolution_json, auto_confidence,
                ),
            )

        result = {
            "conflict_id": cid,
            "conflict_type": conflict_type,
            "resolution_status": resolution_status,
        }
        if resolution_status == "auto_resolved":
            result["auto_resolution"] = json.loads(resolution_json)
        return result

    def resolve(
        self,
        conflict_id: str,
        resolution: Dict[str, Any],
        *,
        by: str = "user",
    ) -> bool:
        """Manually resolve a conflict. Returns True if updated."""
        status = "user_resolved" if by == "user" else "auto_resolved"
        with self._tx() as conn:
            result = conn.execute(
                """UPDATE cognitive_conflicts
                   SET resolution_status = ?, resolution_json = ?
                   WHERE conflict_id = ? AND resolution_status = 'open'""",
                (status, json.dumps(resolution), conflict_id),
            )
        return result.rowcount > 0

    def defer(self, conflict_id: str) -> bool:
        """Defer a conflict for later resolution."""
        with self._tx() as conn:
            result = conn.execute(
                """UPDATE cognitive_conflicts
                   SET resolution_status = 'deferred'
                   WHERE conflict_id = ? AND resolution_status = 'open'""",
                (conflict_id,),
            )
        return result.rowcount > 0

    def get(self, conflict_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cognitive_conflicts WHERE conflict_id = ?",
                (conflict_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_open(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all open (unresolved) conflicts."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM cognitive_conflicts
                   WHERE resolution_status = 'open'
                   ORDER BY detected_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_for_object(
        self, object_type: str, object_id: str
    ) -> List[Dict[str, Any]]:
        """Get all conflicts involving a specific object."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM cognitive_conflicts
                   WHERE (side_a_type = ? AND side_a_id = ?)
                      OR (side_b_type = ? AND side_b_id = ?)
                   ORDER BY detected_at DESC""",
                (object_type, object_id, object_type, object_id),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_open(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM cognitive_conflicts WHERE resolution_status = 'open'"
            ).fetchone()
        return row[0] if row else 0

    def has_open_conflicts(
        self, object_type: str, object_id: str
    ) -> bool:
        """Check if an object has any open conflicts (for retrieval penalty)."""
        with self._lock:
            row = self._conn.execute(
                """SELECT 1 FROM cognitive_conflicts
                   WHERE resolution_status = 'open'
                   AND ((side_a_type = ? AND side_a_id = ?)
                     OR (side_b_type = ? AND side_b_id = ?))
                   LIMIT 1""",
                (object_type, object_id, object_type, object_id),
            ).fetchone()
        return row is not None

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        resolution = row["resolution_json"]
        if isinstance(resolution, str):
            try:
                resolution = json.loads(resolution)
            except (json.JSONDecodeError, TypeError):
                resolution = None
        return {
            "conflict_id": row["conflict_id"],
            "conflict_type": row["conflict_type"],
            "side_a_type": row["side_a_type"],
            "side_a_id": row["side_a_id"],
            "side_b_type": row["side_b_type"],
            "side_b_id": row["side_b_id"],
            "detected_at": row["detected_at"],
            "resolution_status": row["resolution_status"],
            "resolution": resolution,
            "auto_resolution_confidence": row["auto_resolution_confidence"],
        }
