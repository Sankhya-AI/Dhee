"""Dhee v3 — RawEventStore: immutable source-of-truth memory events.

Every call to remember() writes an immutable raw event. Corrections create
new events with supersedes_event_id pointing to the original. Deletions
mark events as 'deleted' (soft delete — never physically removed).

Design contract:
    - Raw events are never mutated after creation
    - Content-hash dedup prevents duplicate storage of identical content
    - Corrections/deletions change status of the OLD event and create a NEW event
    - All derived cognition traces back to raw events via derived_lineage
    - Zero LLM calls — this is a pure storage layer
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from dhee.core.storage import initialize_schema

logger = logging.getLogger(__name__)


class EventStatus(str, Enum):
    ACTIVE = "active"
    CORRECTED = "corrected"
    DELETED = "deleted"


@dataclass
class RawMemoryEvent:
    """In-memory representation of a raw memory event."""

    event_id: str
    user_id: str
    content: str
    content_hash: str
    status: EventStatus = EventStatus.ACTIVE
    session_id: Optional[str] = None
    source: str = "user"
    supersedes_event_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None

    @staticmethod
    def compute_hash(content: str) -> str:
        """SHA-256 content hash for dedup."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "content": self.content,
            "content_hash": self.content_hash,
            "status": self.status.value,
            "session_id": self.session_id,
            "source": self.source,
            "supersedes_event_id": self.supersedes_event_id,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> str:
    data_dir = os.environ.get("DHEE_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), ".dhee"
    )
    return os.path.join(data_dir, "v3.db")


class RawEventStore:
    """Immutable raw event storage backed by SQLite.

    Thread-safe via RLock. Follows the same connection pattern as
    dhee/db/sqlite.py (_SQLiteBase).
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA cache_size=-8000")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

        # Initialize all v3 tables
        initialize_schema(self._conn)

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None  # type: ignore[assignment]

    @contextmanager
    def _tx(self):
        """Yield connection under lock with commit/rollback."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add(
        self,
        content: str,
        user_id: str,
        *,
        session_id: Optional[str] = None,
        source: str = "user",
        metadata: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> RawMemoryEvent:
        """Store a new raw memory event. Returns the event (existing if dedup hit).

        Content-hash dedup: if identical content already exists for this user
        and is active, returns the existing event instead of creating a duplicate.
        """
        content_hash = RawMemoryEvent.compute_hash(content)
        eid = event_id or str(uuid.uuid4())
        meta = metadata or {}
        now = _utcnow_iso()

        with self._tx() as conn:
            # Dedup check — same content, same user, still active
            existing = conn.execute(
                """SELECT event_id, user_id, content, content_hash, status,
                          session_id, source, supersedes_event_id,
                          metadata_json, created_at
                   FROM raw_memory_events
                   WHERE content_hash = ? AND user_id = ? AND status = 'active'
                   LIMIT 1""",
                (content_hash, user_id),
            ).fetchone()

            if existing:
                return self._row_to_event(existing)

            conn.execute(
                """INSERT INTO raw_memory_events
                   (event_id, user_id, session_id, created_at, content,
                    content_hash, source, status, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
                (
                    eid, user_id, session_id, now, content,
                    content_hash, source, json.dumps(meta),
                ),
            )

        return RawMemoryEvent(
            event_id=eid,
            user_id=user_id,
            content=content,
            content_hash=content_hash,
            status=EventStatus.ACTIVE,
            session_id=session_id,
            source=source,
            metadata=meta,
            created_at=now,
        )

    def correct(
        self,
        original_event_id: str,
        new_content: str,
        *,
        source: str = "user_correction",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RawMemoryEvent:
        """Correct an existing event.

        1. Marks the original event as 'corrected'
        2. Creates a new event with supersedes_event_id pointing to original
        3. Returns the new event

        Raises ValueError if original event not found or not active.
        """
        with self._tx() as conn:
            original = conn.execute(
                """SELECT event_id, user_id, session_id, status
                   FROM raw_memory_events WHERE event_id = ?""",
                (original_event_id,),
            ).fetchone()

            if not original:
                raise ValueError(f"Event not found: {original_event_id}")
            if original["status"] != "active":
                raise ValueError(
                    f"Cannot correct event with status '{original['status']}': "
                    f"{original_event_id}"
                )

            # Mark original as corrected
            conn.execute(
                "UPDATE raw_memory_events SET status = 'corrected' WHERE event_id = ?",
                (original_event_id,),
            )

            # Create correction event
            new_id = str(uuid.uuid4())
            content_hash = RawMemoryEvent.compute_hash(new_content)
            meta = metadata or {}
            now = _utcnow_iso()

            conn.execute(
                """INSERT INTO raw_memory_events
                   (event_id, user_id, session_id, created_at, content,
                    content_hash, source, status, supersedes_event_id, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    new_id, original["user_id"], original["session_id"],
                    now, new_content, content_hash, source,
                    original_event_id, json.dumps(meta),
                ),
            )

        return RawMemoryEvent(
            event_id=new_id,
            user_id=original["user_id"],
            content=new_content,
            content_hash=content_hash,
            status=EventStatus.ACTIVE,
            session_id=original["session_id"],
            source=source,
            supersedes_event_id=original_event_id,
            metadata=meta,
            created_at=now,
        )

    def delete(self, event_id: str) -> bool:
        """Soft-delete a raw event. Returns True if status changed.

        Marks the event as 'deleted'. Does NOT physically remove it.
        Raises ValueError if event not found.
        """
        with self._tx() as conn:
            row = conn.execute(
                "SELECT status FROM raw_memory_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()

            if not row:
                raise ValueError(f"Event not found: {event_id}")
            if row["status"] == "deleted":
                return False

            conn.execute(
                "UPDATE raw_memory_events SET status = 'deleted' WHERE event_id = ?",
                (event_id,),
            )
            return True

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, event_id: str) -> Optional[RawMemoryEvent]:
        """Get a single event by ID."""
        with self._lock:
            row = self._conn.execute(
                """SELECT event_id, user_id, content, content_hash, status,
                          session_id, source, supersedes_event_id,
                          metadata_json, created_at
                   FROM raw_memory_events WHERE event_id = ?""",
                (event_id,),
            ).fetchone()
        return self._row_to_event(row) if row else None

    def get_by_hash(
        self, content_hash: str, user_id: str
    ) -> Optional[RawMemoryEvent]:
        """Get active event by content hash + user."""
        with self._lock:
            row = self._conn.execute(
                """SELECT event_id, user_id, content, content_hash, status,
                          session_id, source, supersedes_event_id,
                          metadata_json, created_at
                   FROM raw_memory_events
                   WHERE content_hash = ? AND user_id = ? AND status = 'active'
                   LIMIT 1""",
                (content_hash, user_id),
            ).fetchone()
        return self._row_to_event(row) if row else None

    def list_by_user(
        self,
        user_id: str,
        *,
        status: Optional[EventStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[RawMemoryEvent]:
        """List events for a user, newest first."""
        with self._lock:
            if status:
                rows = self._conn.execute(
                    """SELECT event_id, user_id, content, content_hash, status,
                              session_id, source, supersedes_event_id,
                              metadata_json, created_at
                       FROM raw_memory_events
                       WHERE user_id = ? AND status = ?
                       ORDER BY created_at DESC
                       LIMIT ? OFFSET ?""",
                    (user_id, status.value, limit, offset),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT event_id, user_id, content, content_hash, status,
                              session_id, source, supersedes_event_id,
                              metadata_json, created_at
                       FROM raw_memory_events
                       WHERE user_id = ?
                       ORDER BY created_at DESC
                       LIMIT ? OFFSET ?""",
                    (user_id, limit, offset),
                ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_supersedes_chain(self, event_id: str) -> List[RawMemoryEvent]:
        """Walk the supersedes chain from newest to oldest.

        Given an event that supersedes another, returns the full chain:
        [newest_correction, ..., original_event]
        """
        chain: List[RawMemoryEvent] = []
        seen: set = set()
        current_id: Optional[str] = event_id

        while current_id and current_id not in seen:
            seen.add(current_id)
            event = self.get(current_id)
            if not event:
                break
            chain.append(event)
            current_id = event.supersedes_event_id

        return chain

    def count(
        self, user_id: str, *, status: Optional[EventStatus] = None
    ) -> int:
        """Count events for a user."""
        with self._lock:
            if status:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM raw_memory_events WHERE user_id = ? AND status = ?",
                    (user_id, status.value),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM raw_memory_events WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
        return row[0] if row else 0

    def get_events_since(
        self, user_id: str, since_iso: str, *, status: Optional[EventStatus] = None
    ) -> List[RawMemoryEvent]:
        """Get events created after a given ISO timestamp."""
        with self._lock:
            if status:
                rows = self._conn.execute(
                    """SELECT event_id, user_id, content, content_hash, status,
                              session_id, source, supersedes_event_id,
                              metadata_json, created_at
                       FROM raw_memory_events
                       WHERE user_id = ? AND created_at > ? AND status = ?
                       ORDER BY created_at ASC""",
                    (user_id, since_iso, status.value),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT event_id, user_id, content, content_hash, status,
                              session_id, source, supersedes_event_id,
                              metadata_json, created_at
                       FROM raw_memory_events
                       WHERE user_id = ? AND created_at > ?
                       ORDER BY created_at ASC""",
                    (user_id, since_iso),
                ).fetchall()
        return [self._row_to_event(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> RawMemoryEvent:
        meta_raw = row["metadata_json"]
        if isinstance(meta_raw, str):
            try:
                meta = json.loads(meta_raw)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        elif isinstance(meta_raw, dict):
            meta = meta_raw
        else:
            meta = {}

        return RawMemoryEvent(
            event_id=row["event_id"],
            user_id=row["user_id"],
            content=row["content"],
            content_hash=row["content_hash"],
            status=EventStatus(row["status"]),
            session_id=row["session_id"],
            source=row["source"],
            supersedes_event_id=row["supersedes_event_id"],
            metadata=meta,
            created_at=row["created_at"],
        )
