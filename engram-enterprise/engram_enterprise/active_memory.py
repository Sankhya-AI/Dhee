"""
Active Memory Store — real-time signal bus for multi-agent coordination.

Signals are ephemeral messages with TTL tiers that auto-expire.
Uses a separate SQLite database with WAL mode for concurrent access.
"""

import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from engram.configs.active import ActiveMemoryConfig

logger = logging.getLogger(__name__)

# TTL tier ordering for display priority (highest first)
_TIER_PRIORITY = {"directive": 0, "critical": 1, "notable": 2, "noise": 3}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


class ActiveMemoryStore:
    """SQLite-backed signal bus for active memory."""

    def __init__(self, config: Optional[ActiveMemoryConfig] = None):
        self.config = config or ActiveMemoryConfig()
        db_path = os.path.expanduser(self.config.db_path)
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    signal_type TEXT NOT NULL CHECK (signal_type IN ('state', 'event', 'directive')),
                    scope TEXT NOT NULL DEFAULT 'global' CHECK (scope IN ('global', 'repo', 'namespace')),
                    scope_key TEXT,
                    ttl_tier TEXT NOT NULL DEFAULT 'notable',
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source_agent_id TEXT,
                    user_id TEXT DEFAULT 'default',
                    read_count INTEGER DEFAULT 0,
                    read_by TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT,
                    consolidated INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_signals_scope ON signals(scope, scope_key);
                CREATE INDEX IF NOT EXISTS idx_signals_key ON signals(key);
                CREATE INDEX IF NOT EXISTS idx_signals_expires ON signals(expires_at);
                CREATE INDEX IF NOT EXISTS idx_signals_user ON signals(user_id);
            """)

    @contextmanager
    def _get_connection(self):
        with self._lock:
            yield self._conn

    def _compute_expires_at(self, ttl_tier: str) -> Optional[str]:
        """Compute expiration timestamp based on TTL tier."""
        ttl_seconds = self.config.ttl_seconds.get(ttl_tier, 0)
        if ttl_seconds <= 0:
            return None  # permanent
        expires = _utcnow() + timedelta(seconds=ttl_seconds)
        return expires.isoformat()

    def write_signal(
        self,
        *,
        key: str,
        value: str,
        signal_type: str = "state",
        scope: str = "global",
        scope_key: Optional[str] = None,
        ttl_tier: Optional[str] = None,
        source_agent_id: Optional[str] = None,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """Write a signal to the active memory bus.

        - state: UPSERT by (key, source_agent_id, scope, scope_key)
        - event: always INSERT new row
        - directive: UPSERT by key, expires_at=NULL (permanent)
        """
        ttl_tier = ttl_tier or self.config.default_ttl_tier
        if signal_type == "directive":
            ttl_tier = "directive"

        expires_at = self._compute_expires_at(ttl_tier)
        now = _utcnow_iso()

        with self._get_connection() as conn:
            if signal_type == "state":
                # Upsert: overwrite existing state signal with same key+agent+scope
                existing = conn.execute(
                    """SELECT id FROM signals
                       WHERE key = ? AND source_agent_id IS ? AND scope = ? AND scope_key IS ?
                         AND signal_type = 'state' AND user_id = ?""",
                    (key, source_agent_id, scope, scope_key, user_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        """UPDATE signals SET value = ?, ttl_tier = ?, expires_at = ?, created_at = ?
                           WHERE id = ?""",
                        (value, ttl_tier, expires_at, now, existing["id"]),
                    )
                    conn.commit()
                    return {"id": existing["id"], "action": "updated", "key": key}

            elif signal_type == "directive":
                # Upsert by key (directives are global per key)
                existing = conn.execute(
                    """SELECT id FROM signals
                       WHERE key = ? AND signal_type = 'directive' AND user_id = ?""",
                    (key, user_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        """UPDATE signals SET value = ?, source_agent_id = ?, scope = ?, scope_key = ?,
                                             created_at = ?, expires_at = NULL
                           WHERE id = ?""",
                        (value, source_agent_id, scope, scope_key, now, existing["id"]),
                    )
                    conn.commit()
                    return {"id": existing["id"], "action": "updated", "key": key}

            # event signals always INSERT; state/directive fall through if no existing row
            signal_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO signals (id, signal_type, scope, scope_key, ttl_tier, key, value,
                                        source_agent_id, user_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (signal_id, signal_type, scope, scope_key, ttl_tier, key, value,
                 source_agent_id, user_id, now, expires_at),
            )
            conn.commit()
            return {"id": signal_id, "action": "created", "key": key}

    def read_signals(
        self,
        *,
        scope: Optional[str] = None,
        scope_key: Optional[str] = None,
        signal_type: Optional[str] = None,
        user_id: str = "default",
        reader_agent_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Read active signals, auto-GC expired, increment read counts."""
        conditions = ["user_id = ?"]
        params: List[Any] = [user_id]

        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        if scope_key is not None:
            conditions.append("scope_key = ?")
            params.append(scope_key)
        if signal_type:
            conditions.append("signal_type = ?")
            params.append(signal_type)

        where = " AND ".join(conditions)
        effective_limit = limit or self.config.max_signals_per_response

        with self._get_connection() as conn:
            # GC expired signals atomically within the same connection context
            now = _utcnow_iso()
            conn.execute(
                "DELETE FROM signals WHERE expires_at IS NOT NULL AND expires_at < ? AND signal_type != 'directive'",
                (now,),
            )

            rows = conn.execute(
                f"""SELECT * FROM signals WHERE {where}
                    ORDER BY
                        CASE ttl_tier
                            WHEN 'directive' THEN 0
                            WHEN 'critical' THEN 1
                            WHEN 'notable' THEN 2
                            WHEN 'noise' THEN 3
                        END,
                        created_at DESC
                    LIMIT ?""",
                params + [effective_limit],
            ).fetchall()

            results = []
            ids_to_update = []
            for row in rows:
                signal = dict(row)
                # Parse read_by JSON
                try:
                    signal["read_by"] = json.loads(signal.get("read_by", "[]"))
                except (json.JSONDecodeError, TypeError):
                    signal["read_by"] = []
                results.append(signal)
                ids_to_update.append(signal["id"])

                # Track reader
                if reader_agent_id and reader_agent_id not in signal["read_by"]:
                    signal["read_by"].append(reader_agent_id)

            # Batch update read counts
            if ids_to_update:
                for signal in results:
                    conn.execute(
                        "UPDATE signals SET read_count = read_count + 1, read_by = ? WHERE id = ?",
                        (json.dumps(signal["read_by"]), signal["id"]),
                    )
            conn.commit()

        return results

    def peek_signals(
        self,
        *,
        scope: Optional[str] = None,
        scope_key: Optional[str] = None,
        signal_type: Optional[str] = None,
        user_id: str = "default",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Read active signals without incrementing read_count or tracking readers.

        Identical to ``read_signals`` but read-only — no writes are issued,
        making it safe for automatic injection paths that should not inflate
        read counts.
        """
        conditions = ["user_id = ?"]
        params: List[Any] = [user_id]

        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        if scope_key is not None:
            conditions.append("scope_key = ?")
            params.append(scope_key)
        if signal_type:
            conditions.append("signal_type = ?")
            params.append(signal_type)

        where = " AND ".join(conditions)
        effective_limit = limit or self.config.max_signals_per_response

        with self._get_connection() as conn:
            # GC expired signals atomically within the same connection context
            now = _utcnow_iso()
            conn.execute(
                "DELETE FROM signals WHERE expires_at IS NOT NULL AND expires_at < ? AND signal_type != 'directive'",
                (now,),
            )
            conn.commit()

            rows = conn.execute(
                f"""SELECT * FROM signals WHERE {where}
                    ORDER BY
                        CASE ttl_tier
                            WHEN 'directive' THEN 0
                            WHEN 'critical' THEN 1
                            WHEN 'notable' THEN 2
                            WHEN 'noise' THEN 3
                        END,
                        created_at DESC
                    LIMIT ?""",
                params + [effective_limit],
            ).fetchall()

            results = []
            for row in rows:
                signal = dict(row)
                try:
                    signal["read_by"] = json.loads(signal.get("read_by", "[]"))
                except (json.JSONDecodeError, TypeError):
                    signal["read_by"] = []
                results.append(signal)

        return results

    def clear_signals(
        self,
        *,
        key: Optional[str] = None,
        scope: Optional[str] = None,
        scope_key: Optional[str] = None,
        source_agent_id: Optional[str] = None,
        signal_type: Optional[str] = None,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """Clear signals matching the given criteria."""
        conditions = ["user_id = ?"]
        params: List[Any] = [user_id]

        if key:
            conditions.append("key = ?")
            params.append(key)
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        if scope_key is not None:
            conditions.append("scope_key = ?")
            params.append(scope_key)
        if source_agent_id:
            conditions.append("source_agent_id = ?")
            params.append(source_agent_id)
        if signal_type:
            conditions.append("signal_type = ?")
            params.append(signal_type)

        where = " AND ".join(conditions)

        with self._get_connection() as conn:
            cursor = conn.execute(f"DELETE FROM signals WHERE {where}", params)
            conn.commit()
            return {"deleted": cursor.rowcount}

    def gc_expired(self) -> int:
        """Garbage collect expired signals (except directives which never expire)."""
        now = _utcnow_iso()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM signals WHERE expires_at IS NOT NULL AND expires_at < ? AND signal_type != 'directive'",
                (now,),
            )
            conn.commit()
            return cursor.rowcount

    def get_consolidation_candidates(
        self,
        *,
        min_age_seconds: Optional[int] = None,
        min_reads: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get signals eligible for consolidation to passive memory."""
        min_age = min_age_seconds if min_age_seconds is not None else self.config.consolidation_min_age_seconds
        min_reads_val = min_reads if min_reads is not None else self.config.consolidation_min_reads
        cutoff = (_utcnow() - timedelta(seconds=min_age)).isoformat()

        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM signals
                   WHERE consolidated = 0
                     AND created_at < ?
                     AND (
                         signal_type = 'directive'
                         OR ttl_tier = 'critical'
                         OR read_count >= ?
                     )
                   ORDER BY created_at ASC""",
                (cutoff, min_reads_val),
            ).fetchall()

        results = []
        for row in rows:
            signal = dict(row)
            try:
                signal["read_by"] = json.loads(signal.get("read_by", "[]"))
            except (json.JSONDecodeError, TypeError):
                signal["read_by"] = []
            results.append(signal)
        return results

    def mark_consolidated(self, signal_ids: List[str]) -> None:
        """Mark signals as consolidated (promoted to passive memory)."""
        if not signal_ids:
            return
        with self._get_connection() as conn:
            placeholders = ",".join("?" for _ in signal_ids)
            conn.execute(
                f"UPDATE signals SET consolidated = 1 WHERE id IN ({placeholders})",
                signal_ids,
            )
            conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None  # type: ignore[assignment]
