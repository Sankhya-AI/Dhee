from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .schema import CaptureEvent, CapturePolicy, CaptureSession


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaptureStore:
    """Stores desktop capture sessions, raw events, and per-app policies."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    @contextmanager
    def _tx(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _init_db(self) -> None:
        with self._tx() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS capture_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    source_app TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_capture_sessions_user_started
                    ON capture_sessions(user_id, started_at DESC);

                CREATE TABLE IF NOT EXISTS capture_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    source_app TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    text_payload TEXT NOT NULL,
                    structured_payload_json TEXT NOT NULL,
                    window_title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_payload_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_kind TEXT NOT NULL,
                    world_ptr TEXT,
                    memory_id TEXT,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES capture_sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_capture_events_session_created
                    ON capture_events(session_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_capture_events_user_created
                    ON capture_events(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS capture_policies (
                    source_app TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def start_session(
        self,
        *,
        user_id: str = "default",
        source_app: str,
        namespace: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CaptureSession:
        session = CaptureSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            source_app=source_app,
            namespace=namespace,
            status="active",
            started_at=_utcnow(),
            metadata=dict(metadata or {}),
        )
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO capture_sessions (
                    id, user_id, source_app, namespace, status, started_at, ended_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.user_id,
                    session.source_app,
                    session.namespace,
                    session.status,
                    session.started_at,
                    None,
                    json.dumps(session.metadata),
                ),
            )
        return session

    def end_session(
        self,
        session_id: str,
        *,
        status: str = "completed",
        metadata_updates: Optional[Dict[str, Any]] = None,
    ) -> Optional[CaptureSession]:
        existing = self.get_session(session_id)
        if not existing:
            return None
        metadata = dict(existing.metadata)
        metadata.update(metadata_updates or {})
        ended_at = _utcnow()
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE capture_sessions
                SET status = ?, ended_at = ?, metadata_json = ?
                WHERE id = ?
                """,
                (status, ended_at, json.dumps(metadata), session_id),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> Optional[CaptureSession]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM capture_sessions WHERE id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        return _row_to_session(row) if row else None

    def list_sessions(
        self,
        *,
        user_id: str = "default",
        source_app: Optional[str] = None,
        limit: int = 20,
        status: Optional[str] = None,
    ) -> List[CaptureSession]:
        query = "SELECT * FROM capture_sessions WHERE user_id = ?"
        params: List[Any] = [user_id]
        if source_app:
            query += " AND source_app = ?"
            params.append(source_app)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(int(limit))
        with self._tx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_session(row) for row in rows]

    def record_event(self, event: CaptureEvent) -> CaptureEvent:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO capture_events (
                    id, session_id, user_id, source_app, namespace, event_type,
                    created_at, text_payload, structured_payload_json, window_title,
                    url, action_type, action_payload_json, confidence, source_kind,
                    world_ptr, memory_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.session_id,
                    event.user_id,
                    event.source_app,
                    event.namespace,
                    event.event_type,
                    event.created_at,
                    event.text_payload,
                    json.dumps(event.structured_payload),
                    event.window_title,
                    event.url,
                    event.action_type,
                    json.dumps(event.action_payload),
                    float(event.confidence),
                    event.source_kind,
                    event.world_ptr,
                    event.memory_id,
                    json.dumps(event.metadata),
                ),
            )
        return event

    def list_events(
        self,
        *,
        user_id: str = "default",
        session_id: Optional[str] = None,
        source_app: Optional[str] = None,
        limit: int = 50,
    ) -> List[CaptureEvent]:
        query = "SELECT * FROM capture_events WHERE user_id = ?"
        params: List[Any] = [user_id]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if source_app:
            query += " AND source_app = ?"
            params.append(source_app)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        with self._tx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def list_policies(self) -> List[CapturePolicy]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM capture_policies ORDER BY source_app ASC"
            ).fetchall()
        return [_row_to_policy(row) for row in rows]

    def upsert_policy(
        self,
        *,
        source_app: str,
        enabled: bool,
        mode: str = "sampled",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CapturePolicy:
        now = _utcnow()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO capture_policies (source_app, enabled, mode, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_app) DO UPDATE SET
                    enabled = excluded.enabled,
                    mode = excluded.mode,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (source_app, 1 if enabled else 0, mode, json.dumps(metadata or {}), now),
            )
        return self.get_policy(source_app) or CapturePolicy(source_app=source_app, enabled=enabled, mode=mode)

    def get_policy(self, source_app: str) -> Optional[CapturePolicy]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM capture_policies WHERE source_app = ? LIMIT 1",
                (source_app,),
            ).fetchone()
        return _row_to_policy(row) if row else None


def _row_to_session(row: sqlite3.Row) -> CaptureSession:
    return CaptureSession(
        id=row["id"],
        user_id=row["user_id"],
        source_app=row["source_app"],
        namespace=row["namespace"],
        status=row["status"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        metadata=_loads_dict(row["metadata_json"]),
    )


def _row_to_event(row: sqlite3.Row) -> CaptureEvent:
    return CaptureEvent(
        id=row["id"],
        session_id=row["session_id"],
        user_id=row["user_id"],
        source_app=row["source_app"],
        namespace=row["namespace"],
        event_type=row["event_type"],
        created_at=row["created_at"],
        text_payload=row["text_payload"],
        structured_payload=_loads_dict(row["structured_payload_json"]),
        window_title=row["window_title"],
        url=row["url"],
        action_type=row["action_type"],
        action_payload=_loads_dict(row["action_payload_json"]),
        confidence=float(row["confidence"]),
        source_kind=row["source_kind"],
        world_ptr=row["world_ptr"],
        memory_id=row["memory_id"],
        metadata=_loads_dict(row["metadata_json"]),
    )


def _row_to_policy(row: sqlite3.Row) -> CapturePolicy:
    return CapturePolicy(
        source_app=row["source_app"],
        enabled=bool(int(row["enabled"])),
        mode=row["mode"],
        metadata=_loads_dict(row["metadata_json"]),
    )


def _loads_dict(raw: str) -> Dict[str, Any]:
    value = json.loads(raw or "{}")
    return value if isinstance(value, dict) else {}
