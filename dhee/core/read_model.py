"""Dhee v3 — Read Model: materialized retrieval view + delta overlay.

Writes are normalized across type-specific tables. Reads are fast via
a precomputed retrieval_view table plus a delta overlay of recent changes
not yet folded in.

Design contract:
    - retrieval_view is a real table (materialized), not a SQL VIEW
    - Delta overlay covers raw events + derived objects created since last refresh
    - Hot-path retrieval queries the view + delta, fuses results
    - View refresh is a cold-path job (recompute_retrieval_view)
    - Zero LLM calls
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Schema for the materialized retrieval view
RETRIEVAL_VIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS retrieval_view (
    row_id          TEXT PRIMARY KEY,
    source_kind     TEXT NOT NULL CHECK (source_kind IN ('raw', 'distilled', 'episodic')),
    source_type     TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    retrieval_text  TEXT NOT NULL,
    summary         TEXT,
    anchor_era      TEXT,
    anchor_place    TEXT,
    anchor_activity TEXT,
    confidence      REAL DEFAULT 1.0,
    utility         REAL DEFAULT 0.0,
    status          TEXT DEFAULT 'active',
    created_at      TEXT NOT NULL,
    refreshed_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rv_user_kind ON retrieval_view(user_id, source_kind);
CREATE INDEX IF NOT EXISTS idx_rv_source ON retrieval_view(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_rv_status ON retrieval_view(status) WHERE status != 'active';
"""


class ReadModel:
    """Materialized retrieval view with delta overlay.

    Usage:
        model = ReadModel(conn, lock)
        model.refresh(events, beliefs, policies, ...)  # cold-path
        results = model.query(user_id, limit=20)        # hot-path
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock
        self._ensure_schema()
        self._last_refresh: Optional[str] = None

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(RETRIEVAL_VIEW_SCHEMA)
            self._conn.commit()

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Cold-path: refresh the materialized view
    # ------------------------------------------------------------------

    def refresh(
        self,
        user_id: str,
        *,
        events_store: Optional[Any] = None,
        beliefs_store: Optional[Any] = None,
        policies_store: Optional[Any] = None,
        insights_store: Optional[Any] = None,
        heuristics_store: Optional[Any] = None,
        anchors_store: Optional[Any] = None,
    ) -> Dict[str, int]:
        """Rebuild the retrieval view for a user. Cold-path operation.

        Returns counts of rows refreshed per source type.
        """
        now = _utcnow_iso()
        counts: Dict[str, int] = {}

        with self._tx() as conn:
            # Clear existing rows for this user
            conn.execute(
                "DELETE FROM retrieval_view WHERE user_id = ?",
                (user_id,),
            )

            # Raw events
            if events_store:
                from dhee.core.events import EventStatus
                events = events_store.list_by_user(
                    user_id, status=EventStatus.ACTIVE, limit=5000
                )
                for e in events:
                    conn.execute(
                        """INSERT INTO retrieval_view
                           (row_id, source_kind, source_type, source_id,
                            user_id, retrieval_text, confidence,
                            status, created_at, refreshed_at)
                           VALUES (?, 'raw', 'event', ?, ?, ?, 1.0, 'active', ?, ?)""",
                        (
                            f"raw:{e.event_id}", e.event_id, user_id,
                            e.content, e.created_at or now, now,
                        ),
                    )
                counts["raw_events"] = len(events)

            # Beliefs
            if beliefs_store:
                beliefs = beliefs_store.list_by_user(user_id, limit=1000)
                for b in beliefs:
                    if b["status"] in ("invalidated",):
                        continue
                    conn.execute(
                        """INSERT INTO retrieval_view
                           (row_id, source_kind, source_type, source_id,
                            user_id, retrieval_text, summary, confidence,
                            utility, status, created_at, refreshed_at)
                           VALUES (?, 'distilled', 'belief', ?, ?, ?, ?, ?, 0.0, ?, ?, ?)""",
                        (
                            f"belief:{b['belief_id']}", b["belief_id"],
                            user_id, b["claim"],
                            f"[{b['domain']}] {b['claim']}",
                            b["confidence"], b["status"],
                            b["created_at"], now,
                        ),
                    )
                counts["beliefs"] = len(beliefs)

            # Policies
            if policies_store:
                policies = policies_store.list_by_user(user_id, limit=500)
                for p in policies:
                    if p["status"] in ("invalidated",):
                        continue
                    text = f"{p['name']}: {json.dumps(p['action'])}"
                    conn.execute(
                        """INSERT INTO retrieval_view
                           (row_id, source_kind, source_type, source_id,
                            user_id, retrieval_text, summary, confidence,
                            utility, status, created_at, refreshed_at)
                           VALUES (?, 'distilled', 'policy', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            f"policy:{p['policy_id']}", p["policy_id"],
                            user_id, text, p["name"],
                            1.0, p["utility"], p["status"],
                            p["created_at"], now,
                        ),
                    )
                counts["policies"] = len(policies)

            # Insights
            if insights_store:
                insights = insights_store.list_by_user(user_id, limit=500)
                for i in insights:
                    if i["status"] in ("invalidated",):
                        continue
                    conn.execute(
                        """INSERT INTO retrieval_view
                           (row_id, source_kind, source_type, source_id,
                            user_id, retrieval_text, confidence,
                            utility, status, created_at, refreshed_at)
                           VALUES (?, 'distilled', 'insight', ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            f"insight:{i['insight_id']}", i["insight_id"],
                            user_id, i["content"], i["confidence"],
                            i["utility"], i["status"],
                            i["created_at"], now,
                        ),
                    )
                counts["insights"] = len(insights)

            # Heuristics
            if heuristics_store:
                heuristics = heuristics_store.list_by_user(user_id, limit=500)
                for h in heuristics:
                    if h["status"] in ("invalidated",):
                        continue
                    conn.execute(
                        """INSERT INTO retrieval_view
                           (row_id, source_kind, source_type, source_id,
                            user_id, retrieval_text, confidence,
                            utility, status, created_at, refreshed_at)
                           VALUES (?, 'distilled', 'heuristic', ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            f"heuristic:{h['heuristic_id']}", h["heuristic_id"],
                            user_id, h["content"], h["confidence"],
                            h["utility"], h["status"],
                            h["created_at"], now,
                        ),
                    )
                counts["heuristics"] = len(heuristics)

        self._last_refresh = now
        return counts

    # ------------------------------------------------------------------
    # Hot-path: query the view
    # ------------------------------------------------------------------

    def query(
        self,
        user_id: str,
        *,
        source_kind: Optional[str] = None,
        source_type: Optional[str] = None,
        status_exclude: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query the retrieval view. Returns rows for downstream fusion."""
        query = "SELECT * FROM retrieval_view WHERE user_id = ?"
        params: list = [user_id]

        if source_kind:
            query += " AND source_kind = ?"
            params.append(source_kind)
        if source_type:
            query += " AND source_type = ?"
            params.append(source_type)

        excludes = status_exclude or ["invalidated"]
        for s in excludes:
            query += " AND status != ?"
            params.append(s)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        return [
            {
                "row_id": r["row_id"],
                "source_kind": r["source_kind"],
                "source_type": r["source_type"],
                "source_id": r["source_id"],
                "user_id": r["user_id"],
                "retrieval_text": r["retrieval_text"],
                "summary": r["summary"],
                "anchor_era": r["anchor_era"],
                "anchor_place": r["anchor_place"],
                "anchor_activity": r["anchor_activity"],
                "confidence": r["confidence"],
                "utility": r["utility"],
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_delta(
        self,
        user_id: str,
        since_iso: str,
        *,
        events_store: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Get raw events created since the last refresh.

        These haven't been folded into the retrieval_view yet.
        Used by fusion to overlay recent changes on top of the materialized view.
        """
        if not events_store:
            return []

        from dhee.core.events import EventStatus
        recent = events_store.get_events_since(
            user_id, since_iso, status=EventStatus.ACTIVE
        )
        return [
            {
                "row_id": f"delta:{e.event_id}",
                "source_kind": "raw",
                "source_type": "event",
                "source_id": e.event_id,
                "user_id": e.user_id,
                "retrieval_text": e.content,
                "summary": None,
                "confidence": 1.0,
                "utility": 0.0,
                "status": "active",
                "created_at": e.created_at,
            }
            for e in recent
        ]

    @property
    def last_refresh(self) -> Optional[str]:
        return self._last_refresh

    def row_count(self, user_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM retrieval_view WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row[0] if row else 0
