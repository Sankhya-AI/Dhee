from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from .schema import (
    AUTOMATIC_CAUSAL_EDGE_TYPES,
    CAUSAL_EDGE_STATUSES,
    CAUSAL_SCHEMA_VERSION,
    CHECKPOINT_CAUSAL_EDGE_TYPES,
    CausalEdge,
    CaptureEvent,
    CapturePolicy,
    CaptureSession,
    CheckpointReport,
    EventFrame,
    RawEvent,
    RetrievalTrace,
)


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

                CREATE TABLE IF NOT EXISTS raw_events (
                    id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT,
                    source_app TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    content_ref TEXT,
                    content_hash TEXT,
                    privacy_scope TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    deleted_at TEXT,
                    redacted_at TEXT,
                    redaction_reason TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_raw_events_user_timestamp
                    ON raw_events(user_id, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_raw_events_session_timestamp
                    ON raw_events(session_id, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_raw_events_scope
                    ON raw_events(user_id, privacy_scope);

                CREATE TABLE IF NOT EXISTS event_frames (
                    id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    frame_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source_event_ids_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    privacy_scope TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    redacted_at TEXT,
                    redaction_reason TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_frames_user_created
                    ON event_frames(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS causal_edges (
                    id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    evidence_event_ids_json TEXT NOT NULL,
                    inferred_by TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    privacy_scope TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    redacted_at TEXT,
                    redaction_reason TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_causal_edges_user_type
                    ON causal_edges(user_id, edge_type);
                CREATE INDEX IF NOT EXISTS idx_causal_edges_source
                    ON causal_edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_causal_edges_target
                    ON causal_edges(target_id);

                CREATE TABLE IF NOT EXISTS causal_checkpoint_reports (
                    id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT,
                    time_window_start TEXT,
                    time_window_end TEXT,
                    status TEXT NOT NULL,
                    event_frame_ids_json TEXT NOT NULL,
                    causal_edge_ids_json TEXT NOT NULL,
                    summary_memory_id TEXT,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkpoint_reports_user_created
                    ON causal_checkpoint_reports(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS retrieval_traces (
                    id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    query TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    retrieval_path_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    privacy_scope TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    redacted_at TEXT,
                    redaction_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_retrieval_traces_user_created
                    ON retrieval_traces(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_retrieval_traces_mode
                    ON retrieval_traces(user_id, mode);
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

    def record_raw_event(self, event: RawEvent) -> RawEvent:
        """Append one immutable raw event to the SQLite truth layer."""
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO raw_events (
                    id, schema_version, user_id, session_id, source_app, namespace,
                    event_type, timestamp, content_ref, content_hash, privacy_scope,
                    metadata_json, deleted_at, redacted_at, redaction_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.schema_version or CAUSAL_SCHEMA_VERSION,
                    event.user_id,
                    event.session_id,
                    event.source_app,
                    event.namespace,
                    event.event_type,
                    event.timestamp,
                    event.content_ref,
                    event.content_hash,
                    event.privacy_scope,
                    json.dumps(event.metadata),
                    event.deleted_at,
                    event.redacted_at,
                    event.redaction_reason,
                    _utcnow(),
                ),
            )
        return event

    def get_raw_event(self, event_id: str) -> Optional[RawEvent]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM raw_events WHERE id = ? LIMIT 1",
                (event_id,),
            ).fetchone()
        return _row_to_raw_event(row) if row else None

    def list_raw_events(
        self,
        *,
        user_id: str = "default",
        session_id: Optional[str] = None,
        source_app: Optional[str] = None,
        privacy_scopes: Optional[Iterable[str]] = None,
        limit: int = 100,
        include_deleted: bool = False,
        include_redacted: bool = False,
        order: str = "desc",
    ) -> List[RawEvent]:
        query = "SELECT * FROM raw_events WHERE user_id = ?"
        params: List[Any] = [user_id]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if source_app:
            query += " AND source_app = ?"
            params.append(source_app)
        scopes = [str(scope) for scope in (privacy_scopes or []) if str(scope).strip()]
        if scopes:
            query += f" AND privacy_scope IN ({','.join('?' for _ in scopes)})"
            params.extend(scopes)
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        if not include_redacted:
            query += " AND redacted_at IS NULL"
        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        query += f" ORDER BY timestamp {direction} LIMIT ?"
        params.append(int(limit))
        with self._tx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_raw_event(row) for row in rows]

    def redact_raw_event(
        self,
        event_id: str,
        *,
        redacted_at: Optional[str] = None,
        reason: str = "",
        delete: bool = False,
    ) -> Optional[RawEvent]:
        existing = self.get_raw_event(event_id)
        if not existing:
            return None
        now = redacted_at or _utcnow()
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE raw_events
                SET redacted_at = ?, redaction_reason = ?, deleted_at = COALESCE(?, deleted_at)
                WHERE id = ?
                """,
                (now, reason, now if delete else None, event_id),
            )
            conn.execute(
                """
                UPDATE event_frames
                SET redacted_at = COALESCE(redacted_at, ?),
                    redaction_reason = COALESCE(NULLIF(redaction_reason, ''), ?)
                WHERE source_event_ids_json LIKE ?
                """,
                (now, reason, f'%"{event_id}"%'),
            )
            conn.execute(
                """
                UPDATE causal_edges
                SET redacted_at = COALESCE(redacted_at, ?),
                    redaction_reason = COALESCE(NULLIF(redaction_reason, ''), ?)
                WHERE evidence_event_ids_json LIKE ?
                """,
                (now, reason, f'%"{event_id}"%'),
            )
            conn.execute(
                """
                UPDATE retrieval_traces
                SET redacted_at = COALESCE(redacted_at, ?),
                    redaction_reason = COALESCE(NULLIF(redaction_reason, ''), ?),
                    deleted_at = COALESCE(?, deleted_at)
                WHERE target_id = ?
                   OR retrieval_path_json LIKE ?
                   OR evidence_json LIKE ?
                   OR result_json LIKE ?
                """,
                (
                    now,
                    reason,
                    now if delete else None,
                    event_id,
                    f'%"{event_id}"%',
                    f'%"{event_id}"%',
                    f'%"{event_id}"%',
                ),
            )
        return self.get_raw_event(event_id)

    def add_event_frame(self, frame: EventFrame) -> EventFrame:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO event_frames (
                    id, schema_version, user_id, frame_type, summary,
                    source_event_ids_json, confidence, privacy_scope, created_at,
                    deleted_at, redacted_at, redaction_reason, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    summary = excluded.summary,
                    source_event_ids_json = excluded.source_event_ids_json,
                    confidence = excluded.confidence,
                    privacy_scope = excluded.privacy_scope,
                    deleted_at = excluded.deleted_at,
                    redacted_at = excluded.redacted_at,
                    redaction_reason = excluded.redaction_reason,
                    metadata_json = excluded.metadata_json
                """,
                (
                    frame.id,
                    frame.schema_version or CAUSAL_SCHEMA_VERSION,
                    frame.user_id,
                    frame.frame_type,
                    frame.summary,
                    json.dumps(frame.source_event_ids),
                    float(frame.confidence),
                    frame.privacy_scope,
                    frame.created_at,
                    frame.deleted_at,
                    frame.redacted_at,
                    frame.redaction_reason,
                    json.dumps(frame.metadata),
                ),
            )
        return frame

    def list_event_frames(
        self,
        *,
        user_id: str = "default",
        limit: int = 100,
        include_deleted: bool = False,
        include_redacted: bool = False,
    ) -> List[EventFrame]:
        query = "SELECT * FROM event_frames WHERE user_id = ?"
        params: List[Any] = [user_id]
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        if not include_redacted:
            query += " AND redacted_at IS NULL"
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        with self._tx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_event_frame(row) for row in rows]

    def add_causal_edge(self, edge: CausalEdge) -> CausalEdge:
        allowed_types = AUTOMATIC_CAUSAL_EDGE_TYPES | CHECKPOINT_CAUSAL_EDGE_TYPES
        if edge.edge_type not in allowed_types:
            raise ValueError(f"Unsupported causal edge type: {edge.edge_type}")
        if edge.status not in CAUSAL_EDGE_STATUSES:
            raise ValueError(f"Unsupported causal edge status: {edge.status}")
        if edge.edge_type == "CAUSED" and not edge.evidence_event_ids:
            raise ValueError("CAUSED edges require evidence_event_ids")
        if not edge.evidence_event_ids and edge.edge_type not in AUTOMATIC_CAUSAL_EDGE_TYPES:
            raise ValueError(f"{edge.edge_type} edges require evidence_event_ids")
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO causal_edges (
                    id, schema_version, user_id, source_id, target_id, edge_type,
                    confidence, status, evidence_event_ids_json, inferred_by,
                    explanation, privacy_scope, created_at, deleted_at, redacted_at,
                    redaction_reason, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    confidence = excluded.confidence,
                    status = excluded.status,
                    evidence_event_ids_json = excluded.evidence_event_ids_json,
                    inferred_by = excluded.inferred_by,
                    explanation = excluded.explanation,
                    privacy_scope = excluded.privacy_scope,
                    deleted_at = excluded.deleted_at,
                    redacted_at = excluded.redacted_at,
                    redaction_reason = excluded.redaction_reason,
                    metadata_json = excluded.metadata_json
                """,
                (
                    edge.id,
                    edge.schema_version or CAUSAL_SCHEMA_VERSION,
                    edge.user_id,
                    edge.source_id,
                    edge.target_id,
                    edge.edge_type,
                    float(edge.confidence),
                    edge.status,
                    json.dumps(edge.evidence_event_ids),
                    edge.inferred_by,
                    edge.explanation,
                    edge.privacy_scope,
                    edge.created_at,
                    edge.deleted_at,
                    edge.redacted_at,
                    edge.redaction_reason,
                    json.dumps(edge.metadata),
                ),
            )
        return edge

    def list_causal_edges(
        self,
        *,
        user_id: str = "default",
        edge_type: Optional[str] = None,
        limit: int = 200,
        include_deleted: bool = False,
        include_redacted: bool = False,
    ) -> List[CausalEdge]:
        query = "SELECT * FROM causal_edges WHERE user_id = ?"
        params: List[Any] = [user_id]
        if edge_type:
            query += " AND edge_type = ?"
            params.append(edge_type)
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        if not include_redacted:
            query += " AND redacted_at IS NULL"
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        with self._tx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_causal_edge(row) for row in rows]

    def add_checkpoint_report(self, report: CheckpointReport) -> CheckpointReport:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO causal_checkpoint_reports (
                    id, schema_version, user_id, session_id, time_window_start,
                    time_window_end, status, event_frame_ids_json,
                    causal_edge_ids_json, summary_memory_id, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.id,
                    report.schema_version or CAUSAL_SCHEMA_VERSION,
                    report.user_id,
                    report.session_id,
                    report.time_window_start,
                    report.time_window_end,
                    report.status,
                    json.dumps(report.event_frame_ids),
                    json.dumps(report.causal_edge_ids),
                    report.summary_memory_id,
                    json.dumps(report.report),
                    report.created_at,
                ),
            )
        return report

    def add_retrieval_trace(self, trace: RetrievalTrace) -> RetrievalTrace:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO retrieval_traces (
                    id, schema_version, user_id, mode, scope, query, target_id,
                    retrieval_path_json, evidence_json, result_json, privacy_scope,
                    metadata_json, created_at, deleted_at, redacted_at, redaction_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    retrieval_path_json = excluded.retrieval_path_json,
                    evidence_json = excluded.evidence_json,
                    result_json = excluded.result_json,
                    privacy_scope = excluded.privacy_scope,
                    metadata_json = excluded.metadata_json,
                    deleted_at = excluded.deleted_at,
                    redacted_at = excluded.redacted_at,
                    redaction_reason = excluded.redaction_reason
                """,
                (
                    trace.id,
                    trace.schema_version or CAUSAL_SCHEMA_VERSION,
                    trace.user_id,
                    trace.mode,
                    trace.scope,
                    trace.query,
                    trace.target_id,
                    json.dumps(trace.retrieval_path),
                    json.dumps(trace.evidence),
                    json.dumps(trace.result),
                    trace.privacy_scope,
                    json.dumps(trace.metadata),
                    trace.created_at,
                    trace.deleted_at,
                    trace.redacted_at,
                    trace.redaction_reason,
                ),
            )
        return trace

    def get_retrieval_trace(
        self,
        trace_id: str,
        *,
        include_deleted: bool = False,
        include_redacted: bool = False,
    ) -> Optional[RetrievalTrace]:
        query = "SELECT * FROM retrieval_traces WHERE id = ?"
        params: List[Any] = [trace_id]
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        if not include_redacted:
            query += " AND redacted_at IS NULL"
        query += " LIMIT 1"
        with self._tx() as conn:
            row = conn.execute(query, params).fetchone()
        return _row_to_retrieval_trace(row) if row else None

    def prune_retrieval_traces(
        self,
        *,
        user_id: str = "default",
        older_than_days: Optional[int] = None,
        keep_latest: int = 1000,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        keep_count = max(0, int(keep_latest or 0))
        cutoff = None
        if older_than_days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max(0, int(older_than_days)))).isoformat()

        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, redacted_at, deleted_at
                FROM retrieval_traces
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
            protected_ids = {str(row["id"]) for row in rows[:keep_count]}
            candidates: List[str] = []
            for row in rows:
                trace_id = str(row["id"])
                if trace_id in protected_ids:
                    continue
                outside_keep = keep_count >= 0
                old_enough = bool(cutoff and str(row["created_at"] or "") < cutoff)
                if cutoff is None:
                    should_prune = outside_keep
                else:
                    should_prune = old_enough
                if should_prune:
                    candidates.append(trace_id)
            if candidates and not dry_run:
                conn.execute(
                    f"DELETE FROM retrieval_traces WHERE id IN ({','.join('?' for _ in candidates)})",
                    candidates,
                )

        return {
            "user_id": user_id,
            "dry_run": bool(dry_run),
            "older_than_days": older_than_days,
            "keep_latest": keep_count,
            "total_traces": len(rows),
            "protected_latest": min(keep_count, len(rows)),
            "candidate_count": len(candidates),
            "pruned_count": 0 if dry_run else len(candidates),
            "candidate_ids": candidates[:50],
        }


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


def _row_to_raw_event(row: sqlite3.Row) -> RawEvent:
    return RawEvent(
        id=row["id"],
        schema_version=row["schema_version"],
        user_id=row["user_id"],
        session_id=row["session_id"],
        source_app=row["source_app"],
        namespace=row["namespace"],
        event_type=row["event_type"],
        timestamp=row["timestamp"],
        content_ref=row["content_ref"],
        content_hash=row["content_hash"],
        privacy_scope=row["privacy_scope"],
        metadata=_loads_dict(row["metadata_json"]),
        deleted_at=row["deleted_at"],
        redacted_at=row["redacted_at"],
        redaction_reason=row["redaction_reason"],
    )


def _row_to_event_frame(row: sqlite3.Row) -> EventFrame:
    return EventFrame(
        id=row["id"],
        schema_version=row["schema_version"],
        user_id=row["user_id"],
        frame_type=row["frame_type"],
        summary=row["summary"],
        source_event_ids=[str(item) for item in _loads_list(row["source_event_ids_json"])],
        confidence=float(row["confidence"]),
        privacy_scope=row["privacy_scope"],
        created_at=row["created_at"],
        deleted_at=row["deleted_at"],
        redacted_at=row["redacted_at"],
        redaction_reason=row["redaction_reason"],
        metadata=_loads_dict(row["metadata_json"]),
    )


def _row_to_causal_edge(row: sqlite3.Row) -> CausalEdge:
    return CausalEdge(
        id=row["id"],
        schema_version=row["schema_version"],
        user_id=row["user_id"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        edge_type=row["edge_type"],
        confidence=float(row["confidence"]),
        status=row["status"],
        evidence_event_ids=[str(item) for item in _loads_list(row["evidence_event_ids_json"])],
        inferred_by=row["inferred_by"],
        explanation=row["explanation"],
        privacy_scope=row["privacy_scope"],
        created_at=row["created_at"],
        deleted_at=row["deleted_at"],
        redacted_at=row["redacted_at"],
        redaction_reason=row["redaction_reason"],
        metadata=_loads_dict(row["metadata_json"]),
    )


def _row_to_retrieval_trace(row: sqlite3.Row) -> RetrievalTrace:
    return RetrievalTrace(
        id=row["id"],
        schema_version=row["schema_version"],
        user_id=row["user_id"],
        mode=row["mode"],
        scope=row["scope"],
        query=row["query"],
        target_id=row["target_id"],
        retrieval_path=[
            item for item in _loads_list(row["retrieval_path_json"]) if isinstance(item, dict)
        ],
        evidence=[
            item for item in _loads_list(row["evidence_json"]) if isinstance(item, dict)
        ],
        result=_loads_dict(row["result_json"]),
        privacy_scope=row["privacy_scope"],
        metadata=_loads_dict(row["metadata_json"]),
        created_at=row["created_at"],
        deleted_at=row["deleted_at"],
        redacted_at=row["redacted_at"],
        redaction_reason=row["redaction_reason"],
    )


def _loads_dict(raw: str) -> Dict[str, Any]:
    value = json.loads(raw or "{}")
    return value if isinstance(value, dict) else {}


def _loads_list(raw: str) -> List[Any]:
    value = json.loads(raw or "[]")
    return value if isinstance(value, list) else []
