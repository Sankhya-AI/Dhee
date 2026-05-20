"""Temporal fact ledger for long-lived Dhee memory.

Facts are not deleted when they become wrong. They receive validity windows,
invalidation evidence, and an event trail so retrieval can ask "what was true
then?" as well as "what is active now?".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


TEMPORAL_FACT_SCHEMA = "dhee.temporal_fact.v1"
TEMPORAL_FACT_LEDGER_SCHEMA = "dhee.temporal_fact_ledger.v1"
TEMPORAL_FACT_EVENT_SCHEMA = "dhee.temporal_fact_event.v1"
ACTIVE_STATUSES = {"active", "verified"}
INACTIVE_STATUSES = {"invalidated", "superseded", "retracted", "expired", "rejected"}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_@./:-]{2,}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_iso(value: Optional[str], *, fallback_now: bool = False) -> Optional[str]:
    if not value:
        return _now_iso() if fallback_now else None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _stable_hash(payload: Any, length: int = 20) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, default=str)


def _json_load(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _canonical(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _tokens(value: str) -> set[str]:
    normalized = str(value or "").replace("_", " ")
    return {match.lower() for match in _TOKEN_RE.findall(normalized)}


def default_ledger_path() -> Path:
    root = Path(os.environ.get("DHEE_DATA_DIR") or (Path.home() / ".dhee")).expanduser()
    return root / "memory_os" / "temporal_facts.db"


@dataclass
class TemporalFact:
    id: str
    user_id: str
    namespace: str
    subject: str
    predicate: str
    object: str
    fact_text: str
    valid_from: str
    observed_at: str
    confidence: float
    schema_version: str = TEMPORAL_FACT_SCHEMA
    valid_to: Optional[str] = None
    source_scene: str = ""
    source_event_ids: List[str] = field(default_factory=list)
    source_memory_ids: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    contradicted_by: List[str] = field(default_factory=list)
    status: str = "active"
    active: bool = True
    privacy_scope: str = "personal"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    invalidated_at: Optional[str] = None
    invalidation_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["active"] = bool(self.active)
        data["confidence"] = round(float(self.confidence), 4)
        return data


class TemporalFactLedger:
    """SQLite source of truth for temporal facts and invalidation history."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None):
        self.db_path = str(Path(db_path or default_ledger_path()).expanduser())
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        self._conn.close()

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
                CREATE TABLE IF NOT EXISTS temporal_facts (
                    id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    fact_text TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    observed_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_scene TEXT NOT NULL,
                    source_event_ids_json TEXT NOT NULL,
                    source_memory_ids_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    contradicted_by_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    privacy_scope TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    invalidation_reason TEXT,
                    metadata_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_temporal_facts_user_active
                    ON temporal_facts(user_id, namespace, active, observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_temporal_facts_spo
                    ON temporal_facts(user_id, namespace, subject, predicate, active);
                CREATE INDEX IF NOT EXISTS idx_temporal_facts_fingerprint
                    ON temporal_facts(user_id, namespace, fingerprint);

                CREATE TABLE IF NOT EXISTS temporal_fact_events (
                    id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    fact_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(fact_id) REFERENCES temporal_facts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_temporal_fact_events_fact
                    ON temporal_fact_events(fact_id, created_at ASC);
                """
            )

    def _fingerprint(self, *, user_id: str, namespace: str, subject: str, predicate: str, object_value: str, fact_text: str) -> str:
        return _stable_hash(
            {
                "user_id": user_id,
                "namespace": namespace,
                "subject": _canonical(subject),
                "predicate": _canonical(predicate),
                "object": _canonical(object_value) or _canonical(fact_text),
            },
            24,
        )

    def _row_to_fact(self, row: sqlite3.Row) -> TemporalFact:
        return TemporalFact(
            id=str(row["id"]),
            schema_version=str(row["schema_version"]),
            user_id=str(row["user_id"]),
            namespace=str(row["namespace"]),
            subject=str(row["subject"]),
            predicate=str(row["predicate"]),
            object=str(row["object"]),
            fact_text=str(row["fact_text"]),
            valid_from=str(row["valid_from"]),
            valid_to=row["valid_to"],
            observed_at=str(row["observed_at"]),
            confidence=float(row["confidence"]),
            source_scene=str(row["source_scene"] or ""),
            source_event_ids=[str(v) for v in _json_load(row["source_event_ids_json"], [])],
            source_memory_ids=[str(v) for v in _json_load(row["source_memory_ids_json"], [])],
            evidence=[dict(v) for v in _json_load(row["evidence_json"], []) if isinstance(v, dict)],
            contradicted_by=[str(v) for v in _json_load(row["contradicted_by_json"], [])],
            status=str(row["status"]),
            active=bool(row["active"]),
            privacy_scope=str(row["privacy_scope"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            invalidated_at=row["invalidated_at"],
            invalidation_reason=row["invalidation_reason"],
            metadata=dict(_json_load(row["metadata_json"], {})),
            fingerprint=str(row["fingerprint"]),
        )

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        *,
        fact_id: str,
        user_id: str,
        event_type: str,
        actor_id: str = "",
        reason: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "id": "tfe_" + uuid.uuid4().hex[:18],
            "schema_version": TEMPORAL_FACT_EVENT_SCHEMA,
            "fact_id": fact_id,
            "user_id": user_id,
            "event_type": event_type,
            "created_at": _now_iso(),
            "actor_id": actor_id or "",
            "reason": reason or "",
            "payload": dict(payload or {}),
        }
        conn.execute(
            """
            INSERT INTO temporal_fact_events (
                id, schema_version, fact_id, user_id, event_type,
                created_at, actor_id, reason, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                event["schema_version"],
                event["fact_id"],
                event["user_id"],
                event["event_type"],
                event["created_at"],
                event["actor_id"],
                event["reason"],
                _json_dump(event["payload"]),
            ),
        )
        return event

    def assert_fact(
        self,
        *,
        fact_text: str,
        user_id: str = "default",
        namespace: str = "default",
        subject: str = "",
        predicate: str = "",
        object: str = "",
        valid_from: Optional[str] = None,
        valid_to: Optional[str] = None,
        observed_at: Optional[str] = None,
        confidence: float = 0.75,
        source_scene: str = "",
        source_event_ids: Optional[Sequence[str]] = None,
        source_memory_ids: Optional[Sequence[str]] = None,
        evidence: Optional[Sequence[Dict[str, Any]]] = None,
        privacy_scope: str = "personal",
        metadata: Optional[Dict[str, Any]] = None,
        contradicts_fact_ids: Optional[Sequence[str]] = None,
        invalidate_conflicts: bool = True,
        actor_id: str = "",
    ) -> Dict[str, Any]:
        text = str(fact_text or "").strip()
        if not text:
            raise ValueError("fact_text is required")
        observed = _normalize_iso(observed_at, fallback_now=True) or _now_iso()
        start = _normalize_iso(valid_from) or observed
        end = _normalize_iso(valid_to)
        subject_value = str(subject or "").strip() or text
        predicate_value = str(predicate or "").strip() or "states"
        object_value = str(object or "").strip() or text
        now = _now_iso()
        fingerprint = self._fingerprint(
            user_id=user_id,
            namespace=namespace,
            subject=subject_value,
            predicate=predicate_value,
            object_value=object_value,
            fact_text=text,
        )
        fact_id = "tf_" + _stable_hash({"fingerprint": fingerprint, "observed_at": observed, "text": text}, 20)
        event_ids = [str(v) for v in source_event_ids or [] if str(v)]
        memory_ids = [str(v) for v in source_memory_ids or [] if str(v)]
        evidence_rows = [dict(v) for v in evidence or [] if isinstance(v, dict)]
        created: TemporalFact
        invalidated: List[TemporalFact] = []
        with self._tx() as conn:
            existing = conn.execute(
                """
                SELECT * FROM temporal_facts
                WHERE user_id = ? AND namespace = ? AND fingerprint = ?
                ORDER BY observed_at DESC LIMIT 1
                """,
                (user_id, namespace, fingerprint),
            ).fetchone()
            if existing and str(existing["status"]) in ACTIVE_STATUSES and bool(existing["active"]):
                created = self._row_to_fact(existing)
                self._insert_event(
                    conn,
                    fact_id=created.id,
                    user_id=user_id,
                    event_type="REASSERT",
                    actor_id=actor_id,
                    reason="matching active fact already exists",
                    payload={"observed_at": observed, "source_event_ids": event_ids, "source_memory_ids": memory_ids},
                )
                return {
                    "format": TEMPORAL_FACT_LEDGER_SCHEMA,
                    "fact": created.to_dict(),
                    "reused": True,
                    "invalidated": [],
                }

            conn.execute(
                """
                INSERT INTO temporal_facts (
                    id, schema_version, user_id, namespace, subject, predicate, object,
                    fact_text, valid_from, valid_to, observed_at, confidence, source_scene,
                    source_event_ids_json, source_memory_ids_json, evidence_json,
                    contradicted_by_json, status, active, privacy_scope, created_at,
                    updated_at, invalidated_at, invalidation_reason, metadata_json, fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_id,
                    TEMPORAL_FACT_SCHEMA,
                    user_id,
                    namespace,
                    subject_value,
                    predicate_value,
                    object_value,
                    text,
                    start,
                    end,
                    observed,
                    float(confidence),
                    source_scene or "",
                    _json_dump(event_ids),
                    _json_dump(memory_ids),
                    _json_dump(evidence_rows),
                    _json_dump([]),
                    "active",
                    1,
                    privacy_scope,
                    now,
                    now,
                    None,
                    None,
                    _json_dump(metadata or {}),
                    fingerprint,
                ),
            )
            self._insert_event(
                conn,
                fact_id=fact_id,
                user_id=user_id,
                event_type="ASSERT",
                actor_id=actor_id,
                reason="new temporal fact asserted",
                payload={"source_scene": source_scene, "evidence_count": len(evidence_rows)},
            )
            conflict_ids = set(str(v) for v in contradicts_fact_ids or [] if str(v))
            if invalidate_conflicts:
                conflict_rows = conn.execute(
                    """
                    SELECT * FROM temporal_facts
                    WHERE user_id = ? AND namespace = ? AND lower(subject) = lower(?) AND lower(predicate) = lower(?)
                      AND active = 1 AND id != ?
                    ORDER BY observed_at DESC
                    """,
                    (user_id, namespace, subject_value, predicate_value, fact_id),
                ).fetchall()
                for row in conflict_rows:
                    if _canonical(row["object"]) != _canonical(object_value):
                        conflict_ids.add(str(row["id"]))
            for conflict_id in sorted(conflict_ids):
                row = conn.execute("SELECT * FROM temporal_facts WHERE id = ? AND user_id = ?", (conflict_id, user_id)).fetchone()
                if not row:
                    continue
                old = self._row_to_fact(row)
                contradicted = list(old.contradicted_by)
                if fact_id not in contradicted:
                    contradicted.append(fact_id)
                invalidated_at = observed
                conn.execute(
                    """
                    UPDATE temporal_facts
                    SET status = ?, active = 0, valid_to = COALESCE(valid_to, ?),
                        invalidated_at = ?, invalidation_reason = ?, contradicted_by_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        "invalidated",
                        invalidated_at,
                        invalidated_at,
                        f"contradicted_by:{fact_id}",
                        _json_dump(contradicted),
                        now,
                        conflict_id,
                    ),
                )
                self._insert_event(
                    conn,
                    fact_id=conflict_id,
                    user_id=user_id,
                    event_type="INVALIDATE",
                    actor_id=actor_id,
                    reason=f"contradicted_by:{fact_id}",
                    payload={"contradicted_by": fact_id, "replacement_fact": fact_id},
                )
                refreshed = conn.execute("SELECT * FROM temporal_facts WHERE id = ?", (conflict_id,)).fetchone()
                if refreshed:
                    invalidated.append(self._row_to_fact(refreshed))
            row = conn.execute("SELECT * FROM temporal_facts WHERE id = ?", (fact_id,)).fetchone()
            created = self._row_to_fact(row)
        return {
            "format": TEMPORAL_FACT_LEDGER_SCHEMA,
            "fact": created.to_dict(),
            "reused": False,
            "invalidated": [fact.to_dict() for fact in invalidated],
        }

    def invalidate_fact(
        self,
        fact_id: str,
        *,
        user_id: str = "default",
        reason: str = "invalidated",
        contradicted_by: Optional[str] = None,
        invalidated_at: Optional[str] = None,
        actor_id: str = "",
    ) -> Dict[str, Any]:
        when = _normalize_iso(invalidated_at, fallback_now=True) or _now_iso()
        now = _now_iso()
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM temporal_facts WHERE id = ? AND user_id = ?", (fact_id, user_id)).fetchone()
            if not row:
                return {"format": TEMPORAL_FACT_LEDGER_SCHEMA, "ok": False, "error": "fact not found"}
            fact = self._row_to_fact(row)
            contradicted = list(fact.contradicted_by)
            if contradicted_by and contradicted_by not in contradicted:
                contradicted.append(contradicted_by)
            conn.execute(
                """
                UPDATE temporal_facts
                SET status = ?, active = 0, valid_to = COALESCE(valid_to, ?),
                    invalidated_at = ?, invalidation_reason = ?, contradicted_by_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                ("invalidated", when, when, reason, _json_dump(contradicted), now, fact_id),
            )
            self._insert_event(
                conn,
                fact_id=fact_id,
                user_id=user_id,
                event_type="INVALIDATE",
                actor_id=actor_id,
                reason=reason,
                payload={"contradicted_by": contradicted_by, "invalidated_at": when},
            )
            updated = self._row_to_fact(conn.execute("SELECT * FROM temporal_facts WHERE id = ?", (fact_id,)).fetchone())
        return {"format": TEMPORAL_FACT_LEDGER_SCHEMA, "ok": True, "fact": updated.to_dict()}

    def get_fact(self, fact_id: str, *, user_id: Optional[str] = None, include_events: bool = False) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM temporal_facts WHERE id = ?"
        params: List[Any] = [fact_id]
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        row = self._conn.execute(query, params).fetchone()
        if not row:
            return None
        data = self._row_to_fact(row).to_dict()
        if include_events:
            data["events"] = self.events_for_fact(fact_id)
        return data

    def events_for_fact(self, fact_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM temporal_fact_events
            WHERE fact_id = ?
            ORDER BY created_at ASC
            """,
            (fact_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "schema_version": row["schema_version"],
                "fact_id": row["fact_id"],
                "user_id": row["user_id"],
                "event_type": row["event_type"],
                "created_at": row["created_at"],
                "actor_id": row["actor_id"],
                "reason": row["reason"],
                "payload": _json_load(row["payload_json"], {}),
            }
            for row in rows
        ]

    def search(
        self,
        query: str = "",
        *,
        user_id: str = "default",
        namespace: Optional[str] = None,
        active_only: bool = True,
        as_of: Optional[str] = None,
        include_invalidated: bool = False,
        privacy_scope: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        has_explicit_as_of = bool(as_of)
        as_of_norm = _normalize_iso(as_of, fallback_now=True) or _now_iso()
        sql = "SELECT * FROM temporal_facts WHERE user_id = ?"
        params: List[Any] = [user_id]
        if namespace:
            sql += " AND namespace = ?"
            params.append(namespace)
        if privacy_scope:
            sql += " AND privacy_scope = ?"
            params.append(privacy_scope)
        if active_only and not has_explicit_as_of:
            sql += " AND active = 1 AND status IN ('active', 'verified')"
        elif not include_invalidated:
            sql += " AND status NOT IN ('rejected')"
        sql += " ORDER BY observed_at DESC, confidence DESC LIMIT ?"
        params.append(max(1, int(limit or 20)) * 5)
        rows = self._conn.execute(sql, params).fetchall()
        query_tokens = _tokens(query)
        results: List[Dict[str, Any]] = []
        for row in rows:
            fact = self._row_to_fact(row)
            if active_only and not self._active_as_of(fact, as_of_norm):
                continue
            haystack = " ".join([fact.subject, fact.predicate, fact.object, fact.fact_text, json.dumps(fact.metadata, default=str)])
            if query_tokens:
                overlap = len(query_tokens & _tokens(haystack))
                if overlap <= 0:
                    continue
                score = overlap / max(1, len(query_tokens))
            else:
                score = 0.5
            item = fact.to_dict()
            item["score"] = round(float(score) + min(0.25, fact.confidence * 0.25), 4)
            results.append(item)
        results.sort(key=lambda item: (-float(item.get("score") or 0), str(item.get("observed_at") or "")), reverse=False)
        return {
            "format": "dhee_temporal_fact_search.v1",
            "schema_version": TEMPORAL_FACT_LEDGER_SCHEMA,
            "query": query,
            "user_id": user_id,
            "namespace": namespace,
            "active_only": active_only,
            "as_of": as_of_norm,
            "results": results[: max(1, int(limit or 20))],
        }

    def _active_as_of(self, fact: TemporalFact, as_of: str) -> bool:
        if fact.status in {"rejected", "retracted"}:
            return False
        if fact.valid_from and fact.valid_from > as_of:
            return False
        if fact.valid_to and fact.valid_to <= as_of:
            return False
        if fact.status in ACTIVE_STATUSES and fact.active:
            return True
        return bool(fact.valid_to and fact.valid_from <= as_of < fact.valid_to)

    def stats(self, *, user_id: str = "default", namespace: Optional[str] = None) -> Dict[str, Any]:
        sql = "SELECT status, active, COUNT(*) AS count FROM temporal_facts WHERE user_id = ?"
        params: List[Any] = [user_id]
        if namespace:
            sql += " AND namespace = ?"
            params.append(namespace)
        sql += " GROUP BY status, active"
        rows = self._conn.execute(sql, params).fetchall()
        by_status: Dict[str, int] = {}
        active_count = 0
        total = 0
        for row in rows:
            count = int(row["count"])
            status = str(row["status"])
            by_status[status] = by_status.get(status, 0) + count
            if int(row["active"]):
                active_count += count
            total += count
        return {
            "format": "dhee_temporal_fact_stats.v1",
            "schema_version": TEMPORAL_FACT_LEDGER_SCHEMA,
            "user_id": user_id,
            "namespace": namespace,
            "total": total,
            "active": active_count,
            "by_status": by_status,
            "db_path": self.db_path,
        }


def open_default_ledger(db_path: str | os.PathLike[str] | None = None) -> TemporalFactLedger:
    return TemporalFactLedger(db_path)
