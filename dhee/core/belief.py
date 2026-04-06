"""BeliefNode — confidence-tracked facts with audit-friendly persistence.

Beliefs are not raw memories.  They are Dhee's working claims about the world:
confidence-scored, evidence-backed, and persisted in a SQLite belief ledger
with append-only events plus a materialized current-state projection.

Architecture
~~~~~~~~~~~~
- Single *persistent* SQLite connection (WAL mode, opened once in __init__).
- In-memory cache (``self._beliefs``) loaded once on init, maintained by
  mutation methods — no reload-on-every-call overhead.
- ``belief_events`` is the append-only source of truth.
- ``belief_nodes`` is the materialized projection updated in the same txn.
- ``truth_status`` is the single source of truth for epistemic state.
  ``status`` is a read/write property that aliases it for backward compat.

The public API is intentionally unchanged so CognitionKernel, Buddhi, and
existing tests work without modification.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.time()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BeliefStatus(str, Enum):
    PROPOSED = "proposed"
    HELD = "held"
    CHALLENGED = "challenged"
    REVISED = "revised"
    RETRACTED = "retracted"


class BeliefFreshnessStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    SUPERSEDED = "superseded"


class BeliefLifecycleStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    TOMBSTONED = "tombstoned"


class BeliefProtectionLevel(str, Enum):
    NORMAL = "normal"
    PINNED = "pinned"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    """A piece of evidence for or against a belief."""

    id: str
    content: str
    supports: bool
    source: str
    confidence: float
    timestamp: float
    memory_id: Optional[str] = None
    episode_id: Optional[str] = None
    event_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "supports": self.supports,
            "source": self.source,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "memory_id": self.memory_id,
            "episode_id": self.episode_id,
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Evidence":
        return cls(
            id=d["id"],
            content=d["content"],
            supports=bool(d["supports"]),
            source=d.get("source", "memory"),
            confidence=float(d.get("confidence", 0.5)),
            timestamp=float(d.get("timestamp", _now())),
            memory_id=d.get("memory_id"),
            episode_id=d.get("episode_id"),
            event_id=d.get("event_id"),
        )


@dataclass
class BeliefRevision:
    """Record of a belief change."""

    timestamp: float
    old_confidence: float
    new_confidence: float
    old_status: str
    new_status: str
    reason: str
    evidence_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "old_confidence": self.old_confidence,
            "new_confidence": self.new_confidence,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "reason": self.reason,
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BeliefRevision":
        return cls(
            timestamp=float(d["timestamp"]),
            old_confidence=float(d["old_confidence"]),
            new_confidence=float(d["new_confidence"]),
            old_status=str(d["old_status"]),
            new_status=str(d["new_status"]),
            reason=d["reason"],
            evidence_id=d.get("evidence_id"),
        )


@dataclass
class BeliefNode:
    """A confidence-tracked belief about the world.

    ``truth_status`` is the single source of truth for the belief's epistemic
    state.  The ``status`` attribute is a read/write *property* that aliases
    ``truth_status`` — existing callers that read/write ``belief.status``
    continue to work unchanged.
    """

    id: str
    user_id: str
    claim: str
    domain: str
    confidence: float
    created_at: float
    updated_at: float

    evidence: List[Evidence] = field(default_factory=list)
    revisions: List[BeliefRevision] = field(default_factory=list)
    contradicts: List[str] = field(default_factory=list)
    source_memory_ids: List[str] = field(default_factory=list)
    source_episode_ids: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    _claim_keywords: List[str] = field(default_factory=list)

    truth_status: BeliefStatus = BeliefStatus.PROPOSED
    freshness_status: BeliefFreshnessStatus = BeliefFreshnessStatus.CURRENT
    lifecycle_status: BeliefLifecycleStatus = BeliefLifecycleStatus.ACTIVE
    protection_level: BeliefProtectionLevel = BeliefProtectionLevel.NORMAL
    successor_id: Optional[str] = None
    origin: str = "memory"
    last_event_seq: int = 0

    # -- status property (aliases truth_status) ----------------------------

    @property
    def status(self) -> BeliefStatus:  # type: ignore[override]
        return self.truth_status

    @status.setter
    def status(self, value: BeliefStatus) -> None:
        self.truth_status = BeliefStatus(
            value.value if isinstance(value, Enum) else str(value)
        )

    # -- post-init ---------------------------------------------------------

    def __post_init__(self) -> None:
        self.evidence = list(self.evidence or [])
        self.revisions = list(self.revisions or [])
        self.contradicts = list(self.contradicts or [])
        self.source_memory_ids = list(self.source_memory_ids or [])
        self.source_episode_ids = list(self.source_episode_ids or [])
        self.tags = list(self.tags or [])
        self._claim_keywords = list(self._claim_keywords or [])
        if not self._claim_keywords:
            self._claim_keywords = BeliefStore._extract_keywords(self.claim)
        # Normalise enum fields defensively
        self.truth_status = BeliefStatus(
            self.truth_status.value
            if isinstance(self.truth_status, Enum)
            else str(self.truth_status)
        )
        self.freshness_status = BeliefFreshnessStatus(
            self.freshness_status.value
            if isinstance(self.freshness_status, Enum)
            else str(self.freshness_status)
        )
        self.lifecycle_status = BeliefLifecycleStatus(
            self.lifecycle_status.value
            if isinstance(self.lifecycle_status, Enum)
            else str(self.lifecycle_status)
        )
        self.protection_level = BeliefProtectionLevel(
            self.protection_level.value
            if isinstance(self.protection_level, Enum)
            else str(self.protection_level)
        )

    # -- helpers -----------------------------------------------------------

    def is_listable(self) -> bool:
        return self.lifecycle_status == BeliefLifecycleStatus.ACTIVE

    def add_evidence(
        self,
        content: str,
        supports: bool,
        source: str = "memory",
        confidence: float = 0.5,
        memory_id: Optional[str] = None,
        episode_id: Optional[str] = None,
    ) -> Evidence:
        evidence = Evidence(
            id=str(uuid.uuid4()),
            content=content,
            supports=supports,
            source=source,
            confidence=confidence,
            timestamp=_now(),
            memory_id=memory_id,
            episode_id=episode_id,
        )
        self.evidence.append(evidence)

        if memory_id and memory_id not in self.source_memory_ids:
            self.source_memory_ids.append(memory_id)
        if episode_id and episode_id not in self.source_episode_ids:
            self.source_episode_ids.append(episode_id)

        old_confidence = self.confidence
        old_status = self.truth_status.value
        self._update_confidence(supports, confidence)

        delta = abs(self.confidence - old_confidence)
        if delta > 0.01 or self.truth_status.value != old_status:
            self.revisions.append(
                BeliefRevision(
                    timestamp=_now(),
                    old_confidence=old_confidence,
                    new_confidence=self.confidence,
                    old_status=old_status,
                    new_status=self.truth_status.value,
                    reason=(
                        f"{'Supporting' if supports else 'Contradicting'}"
                        f" evidence: {content[:100]}"
                    ),
                    evidence_id=evidence.id,
                )
            )

        self.updated_at = _now()
        return evidence

    def _update_confidence(self, supports: bool, evidence_strength: float) -> None:
        lr = 0.15 * evidence_strength
        if supports:
            self.confidence += lr * (1.0 - self.confidence)
        else:
            self.confidence -= lr * self.confidence

        self.confidence = max(0.0, min(1.0, self.confidence))

        if self.confidence >= 0.7:
            if self.truth_status == BeliefStatus.CHALLENGED:
                self.truth_status = BeliefStatus.REVISED
            elif self.truth_status == BeliefStatus.PROPOSED:
                self.truth_status = BeliefStatus.HELD
        elif self.confidence <= 0.3:
            if self.truth_status in (BeliefStatus.HELD, BeliefStatus.REVISED):
                self.truth_status = BeliefStatus.CHALLENGED
        if self.confidence <= 0.1:
            self.truth_status = BeliefStatus.RETRACTED

    @property
    def supporting_evidence_count(self) -> int:
        return sum(1 for e in self.evidence if e.supports)

    @property
    def contradicting_evidence_count(self) -> int:
        return sum(1 for e in self.evidence if not e.supports)

    @property
    def evidence_ratio(self) -> float:
        total = len(self.evidence)
        if total == 0:
            return 0.5
        return self.supporting_evidence_count / total

    @property
    def stability(self) -> float:
        if len(self.revisions) < 2:
            return 1.0
        recent = self.revisions[-5:]
        deltas = [abs(r.new_confidence - r.old_confidence) for r in recent]
        avg_delta = sum(deltas) / len(deltas)
        return max(0.0, 1.0 - avg_delta * len(recent) / 5)

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "claim": self.claim,
            "domain": self.domain,
            "status": self.truth_status.value,
            "truth_status": self.truth_status.value,
            "freshness_status": self.freshness_status.value,
            "lifecycle_status": self.lifecycle_status.value,
            "protection_level": self.protection_level.value,
            "successor_id": self.successor_id,
            "origin": self.origin,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "evidence": [e.to_dict() for e in self.evidence],
            "revisions": [r.to_dict() for r in self.revisions],
            "contradicts": self.contradicts,
            "source_memory_ids": self.source_memory_ids,
            "source_episode_ids": self.source_episode_ids,
            "tags": self.tags,
            "_claim_keywords": self._claim_keywords,
            "last_event_seq": self.last_event_seq,
        }

    def to_compact(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.id,
            "claim": self.claim[:200],
            "domain": self.domain,
            "confidence": round(self.confidence, 2),
            "status": self.truth_status.value,
            "truth_status": self.truth_status.value,
            "freshness_status": self.freshness_status.value,
            "lifecycle_status": self.lifecycle_status.value,
            "protection_level": self.protection_level.value,
            "origin": self.origin,
            "updated_at": self.updated_at,
            "evidence_for": self.supporting_evidence_count,
            "evidence_against": self.contradicting_evidence_count,
            "stability": round(self.stability, 2),
            "source_count": len(set(self.source_memory_ids + self.source_episode_ids)),
            "contradiction_count": len(self.contradicts),
        }
        if self.contradicts:
            result["has_contradictions"] = True
        return result

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BeliefNode":
        truth = d.get("truth_status", d.get("status", "proposed"))
        return cls(
            id=d["id"],
            user_id=d["user_id"],
            claim=d["claim"],
            domain=d.get("domain", "general"),
            confidence=float(d.get("confidence", 0.5)),
            created_at=float(d.get("created_at", _now())),
            updated_at=float(d.get("updated_at", _now())),
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
            revisions=[BeliefRevision.from_dict(r) for r in d.get("revisions", [])],
            contradicts=list(d.get("contradicts", [])),
            source_memory_ids=list(d.get("source_memory_ids", [])),
            source_episode_ids=list(d.get("source_episode_ids", [])),
            tags=list(d.get("tags", [])),
            _claim_keywords=list(d.get("_claim_keywords", [])),
            truth_status=BeliefStatus(truth),
            freshness_status=BeliefFreshnessStatus(
                d.get("freshness_status", BeliefFreshnessStatus.CURRENT.value)
            ),
            lifecycle_status=BeliefLifecycleStatus(
                d.get("lifecycle_status", BeliefLifecycleStatus.ACTIVE.value)
            ),
            protection_level=BeliefProtectionLevel(
                d.get("protection_level", BeliefProtectionLevel.NORMAL.value)
            ),
            successor_id=d.get("successor_id"),
            origin=d.get("origin", d.get("source", "memory")),
            last_event_seq=int(d.get("last_event_seq", 0)),
        )


# -- Backward-compatible __init__ wrapper ----------------------------------
# The dataclass __init__ no longer has a ``status`` parameter (it was replaced
# by the ``truth_status`` field + a property alias).  External callers and
# tests that pass ``status=`` as a keyword argument must keep working.

_BeliefNode_generated_init = BeliefNode.__init__


def _belief_node_init(self: BeliefNode, *args: Any, **kwargs: Any) -> None:
    if "status" in kwargs:
        status_val = kwargs.pop("status")
        kwargs.setdefault("truth_status", status_val)
    _BeliefNode_generated_init(self, *args, **kwargs)


BeliefNode.__init__ = _belief_node_init  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# BeliefStore
# ---------------------------------------------------------------------------

_VALID_EVENT_TYPES = frozenset({
    "proposed", "reinforced", "challenged", "corrected",
    "marked_stale", "tombstoned", "merged", "split",
    "pinned", "unpinned", "locked", "unlocked",
    "archived", "revived",
})

_EVENT_TYPE_CHECK_SQL = (
    "CHECK (event_type IN ("
    + ", ".join(f"'{t}'" for t in sorted(_VALID_EVENT_TYPES))
    + "))"
)


class BeliefStore:
    """SQLite-backed belief store with append-only audit events.

    Architecture:
    - Single persistent SQLite connection (WAL mode), opened once.
    - In-memory cache ``_beliefs`` loaded on init, kept in sync by mutations.
    - ``belief_events`` is the immutable event log (source of truth).
    - ``belief_nodes`` is the materialized projection, updated atomically
      in the same transaction as the event INSERT.
    """

    CONTRADICTION_THRESHOLD = 0.4
    RETRACTION_THRESHOLD = 0.1
    SCHEMA_VERSION = "v4_belief_event_sourced"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        data_dir: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "beliefs",
        )
        os.makedirs(self._dir, exist_ok=True)
        self._db_path = db_path or os.path.join(self._dir, "beliefs.db")

        # Persistent connection — opened once, closed via .close().
        self._conn: sqlite3.Connection = sqlite3.connect(
            self._db_path, check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA cache_size=-8000")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._lock = threading.RLock()
        self._beliefs: Dict[str, BeliefNode] = {}

        self._ensure_schema()
        self._migrate_legacy_json()
        self._load()

    def close(self) -> None:
        """Close the persistent connection for clean shutdown."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None  # type: ignore[assignment]

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield the persistent connection under the thread lock.

        Commits on clean exit, rolls back on exception.
        """
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Schema and migration
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at REAL DEFAULT (strftime('%s','now'))
                );

                CREATE TABLE IF NOT EXISTS belief_migration_receipts (
                    source_path TEXT PRIMARY KEY,
                    receipt_kind TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS belief_nodes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    truth_status TEXT NOT NULL,
                    freshness_status TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    protection_level TEXT NOT NULL,
                    successor_id TEXT,
                    origin TEXT NOT NULL,
                    claim_keywords TEXT NOT NULL,
                    evidence_for_count INTEGER DEFAULT 0,
                    evidence_against_count INTEGER DEFAULT 0,
                    source_memory_ids TEXT NOT NULL,
                    source_episode_ids TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_event_seq INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_belief_nodes_user
                    ON belief_nodes(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_belief_nodes_domain
                    ON belief_nodes(user_id, domain);
                CREATE INDEX IF NOT EXISTS idx_belief_nodes_statuses
                    ON belief_nodes(user_id, truth_status, freshness_status,
                                    lifecycle_status, protection_level);

                CREATE TABLE IF NOT EXISTS belief_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL,
                    belief_id TEXT NOT NULL,
                    event_type TEXT NOT NULL {_EVENT_TYPE_CHECK_SQL},
                    actor TEXT,
                    reason TEXT,
                    evidence_id TEXT,
                    successor_belief_id TEXT,
                    payload TEXT NOT NULL,
                    confidence_before REAL,
                    confidence_after REAL,
                    truth_status_before TEXT,
                    truth_status_after TEXT,
                    freshness_status_before TEXT,
                    freshness_status_after TEXT,
                    lifecycle_status_before TEXT,
                    lifecycle_status_after TEXT,
                    protection_level_before TEXT,
                    protection_level_after TEXT,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_belief_events_belief
                    ON belief_events(belief_id, seq DESC);

                CREATE TABLE IF NOT EXISTS belief_evidence (
                    id TEXT PRIMARY KEY,
                    belief_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    supports INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    memory_id TEXT,
                    episode_id TEXT,
                    event_id TEXT,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_belief_evidence_belief
                    ON belief_evidence(belief_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS belief_relations (
                    id TEXT PRIMARY KEY,
                    belief_a_id TEXT NOT NULL,
                    belief_b_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (belief_a_id, belief_b_id, relation_type)
                );

                CREATE INDEX IF NOT EXISTS idx_belief_relations_a
                    ON belief_relations(belief_a_id, relation_type);
                CREATE INDEX IF NOT EXISTS idx_belief_relations_b
                    ON belief_relations(belief_b_id, relation_type);

                CREATE TABLE IF NOT EXISTS belief_influence_events (
                    id TEXT PRIMARY KEY,
                    belief_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    influence_type TEXT NOT NULL,
                    query TEXT,
                    session_id TEXT,
                    answer_fragment TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{{}}'
                                                       ,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_belief_influence_belief
                    ON belief_influence_events(belief_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_belief_influence_user
                    ON belief_influence_events(user_id, created_at DESC);
            """)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (self.SCHEMA_VERSION,),
            )

    # ------------------------------------------------------------------
    # Legacy JSON migration
    # ------------------------------------------------------------------

    def _migrate_legacy_json(self) -> None:
        json_files = [
            os.path.join(self._dir, name)
            for name in os.listdir(self._dir)
            if name.endswith(".json")
        ]
        if not json_files:
            return

        backup_dir = os.path.join(self._dir, "legacy_json_backup")
        os.makedirs(backup_dir, exist_ok=True)
        imported: List[Tuple[str, BeliefNode]] = []

        with self._get_connection() as conn:
            for path in json_files:
                receipt = conn.execute(
                    "SELECT 1 FROM belief_migration_receipts"
                    " WHERE source_path = ?",
                    (path,),
                ).fetchone()
                if receipt:
                    continue
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    belief = BeliefNode.from_dict(data)
                except (OSError, json.JSONDecodeError, KeyError) as exc:
                    logger.debug("Skipping legacy belief %s: %s", path, exc)
                    continue
                self._import_legacy_belief(conn, belief)
                conn.execute(
                    "INSERT OR REPLACE INTO belief_migration_receipts"
                    " (source_path, receipt_kind, created_at)"
                    " VALUES (?, ?, ?)",
                    (path, "legacy_json_import", _now()),
                )
                imported.append((path, belief))

            # Relations after all nodes are inserted.
            for _, belief in imported:
                for contra_id in belief.contradicts:
                    if belief.id != contra_id:
                        self._upsert_relation_conn(
                            conn, belief.id, contra_id,
                            "contradicts", sort_pair=True,
                        )

            # Move JSON files to backup while still holding the lock.
            for path, _ in imported:
                dst = os.path.join(backup_dir, os.path.basename(path))
                try:
                    shutil.move(path, dst)
                except OSError as exc:
                    logger.debug("Cannot backup %s: %s", path, exc)

    def _import_legacy_belief(
        self, conn: sqlite3.Connection, belief: BeliefNode,
    ) -> None:
        belief.origin = belief.origin or "memory"
        self._upsert_node_conn(conn, belief)

        if belief.revisions:
            for rev in belief.revisions:
                event_type = (
                    "reinforced"
                    if rev.new_confidence >= rev.old_confidence
                    else "challenged"
                )
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO belief_events (
                        id, belief_id, event_type, actor, reason,
                        evidence_id, successor_belief_id, payload,
                        confidence_before, confidence_after,
                        truth_status_before, truth_status_after,
                        freshness_status_before, freshness_status_after,
                        lifecycle_status_before, lifecycle_status_after,
                        protection_level_before, protection_level_after,
                        created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid.uuid4()),
                        belief.id,
                        event_type,
                        "legacy_import",
                        rev.reason,
                        rev.evidence_id,
                        belief.successor_id,
                        _json_dumps({"source": "legacy_json"}),
                        rev.old_confidence,
                        rev.new_confidence,
                        rev.old_status,
                        rev.new_status,
                        BeliefFreshnessStatus.CURRENT.value,
                        belief.freshness_status.value,
                        BeliefLifecycleStatus.ACTIVE.value,
                        belief.lifecycle_status.value,
                        BeliefProtectionLevel.NORMAL.value,
                        belief.protection_level.value,
                        rev.timestamp,
                    ),
                )
                belief.last_event_seq = max(
                    belief.last_event_seq, int(cur.lastrowid or 0),
                )
        else:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO belief_events (
                    id, belief_id, event_type, actor, reason,
                    evidence_id, successor_belief_id, payload,
                    confidence_before, confidence_after,
                    truth_status_before, truth_status_after,
                    freshness_status_before, freshness_status_after,
                    lifecycle_status_before, lifecycle_status_after,
                    protection_level_before, protection_level_after,
                    created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(uuid.uuid4()),
                    belief.id,
                    "proposed",
                    "legacy_import",
                    "Imported legacy belief",
                    None,
                    belief.successor_id,
                    _json_dumps({"source": "legacy_json"}),
                    belief.confidence,
                    belief.confidence,
                    belief.truth_status.value,
                    belief.truth_status.value,
                    belief.freshness_status.value,
                    belief.freshness_status.value,
                    belief.lifecycle_status.value,
                    belief.lifecycle_status.value,
                    belief.protection_level.value,
                    belief.protection_level.value,
                    belief.created_at,
                ),
            )
            belief.last_event_seq = max(
                belief.last_event_seq, int(cur.lastrowid or 0),
            )

        for evidence in belief.evidence:
            conn.execute(
                """
                INSERT OR REPLACE INTO belief_evidence (
                    id, belief_id, content, supports, source, confidence,
                    memory_id, episode_id, event_id, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    evidence.id, belief.id, evidence.content,
                    1 if evidence.supports else 0, evidence.source,
                    evidence.confidence, evidence.memory_id,
                    evidence.episode_id, evidence.event_id,
                    evidence.timestamp,
                ),
            )

        self._upsert_node_conn(conn, belief)

    # ------------------------------------------------------------------
    # Internal write helpers
    # ------------------------------------------------------------------

    def _snapshot(self, belief: BeliefNode) -> Dict[str, Any]:
        return {
            "confidence": belief.confidence,
            "truth_status": belief.truth_status.value,
            "freshness_status": belief.freshness_status.value,
            "lifecycle_status": belief.lifecycle_status.value,
            "protection_level": belief.protection_level.value,
        }

    def _append_event_conn(
        self,
        conn: sqlite3.Connection,
        belief: BeliefNode,
        event_type: str,
        before_state: Dict[str, Any],
        *,
        actor: str = "system",
        reason: str = "",
        evidence_id: Optional[str] = None,
        successor_belief_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
    ) -> Tuple[str, int]:
        event_id = str(uuid.uuid4())
        ts = float(created_at or _now())
        cur = conn.execute(
            """
            INSERT INTO belief_events (
                id, belief_id, event_type, actor, reason,
                evidence_id, successor_belief_id, payload,
                confidence_before, confidence_after,
                truth_status_before, truth_status_after,
                freshness_status_before, freshness_status_after,
                lifecycle_status_before, lifecycle_status_after,
                protection_level_before, protection_level_after,
                created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                belief.id,
                event_type,
                actor,
                reason,
                evidence_id,
                successor_belief_id,
                _json_dumps(payload or {}),
                before_state.get("confidence"),
                belief.confidence,
                before_state.get("truth_status"),
                belief.truth_status.value,
                before_state.get("freshness_status"),
                belief.freshness_status.value,
                before_state.get("lifecycle_status"),
                belief.lifecycle_status.value,
                before_state.get("protection_level"),
                belief.protection_level.value,
                ts,
            ),
        )
        belief.last_event_seq = int(cur.lastrowid or 0)
        belief.updated_at = max(belief.updated_at, ts)
        self._upsert_node_conn(conn, belief)
        return event_id, belief.last_event_seq

    def _insert_evidence_conn(
        self,
        conn: sqlite3.Connection,
        belief: BeliefNode,
        evidence: Evidence,
        event_id: Optional[str],
    ) -> None:
        evidence.event_id = event_id
        conn.execute(
            """
            INSERT OR REPLACE INTO belief_evidence (
                id, belief_id, content, supports, source, confidence,
                memory_id, episode_id, event_id, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                evidence.id, belief.id, evidence.content,
                1 if evidence.supports else 0, evidence.source,
                evidence.confidence, evidence.memory_id,
                evidence.episode_id, event_id, evidence.timestamp,
            ),
        )

    def _upsert_relation_conn(
        self,
        conn: sqlite3.Connection,
        belief_a_id: str,
        belief_b_id: str,
        relation_type: str,
        *,
        sort_pair: bool = False,
    ) -> None:
        if sort_pair:
            belief_a_id, belief_b_id = sorted([belief_a_id, belief_b_id])
        conn.execute(
            """
            INSERT OR IGNORE INTO belief_relations
                (id, belief_a_id, belief_b_id, relation_type, created_at)
            VALUES (?,?,?,?,?)
            """,
            (str(uuid.uuid4()), belief_a_id, belief_b_id, relation_type, _now()),
        )

    def _upsert_node_conn(
        self, conn: sqlite3.Connection, belief: BeliefNode,
    ) -> None:
        conn.execute(
            """
            INSERT INTO belief_nodes (
                id, user_id, claim, domain, confidence,
                truth_status, freshness_status, lifecycle_status,
                protection_level, successor_id, origin, claim_keywords,
                evidence_for_count, evidence_against_count,
                source_memory_ids, source_episode_ids, tags,
                created_at, updated_at, last_event_seq
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                user_id=excluded.user_id,
                claim=excluded.claim,
                domain=excluded.domain,
                confidence=excluded.confidence,
                truth_status=excluded.truth_status,
                freshness_status=excluded.freshness_status,
                lifecycle_status=excluded.lifecycle_status,
                protection_level=excluded.protection_level,
                successor_id=excluded.successor_id,
                origin=excluded.origin,
                claim_keywords=excluded.claim_keywords,
                evidence_for_count=excluded.evidence_for_count,
                evidence_against_count=excluded.evidence_against_count,
                source_memory_ids=excluded.source_memory_ids,
                source_episode_ids=excluded.source_episode_ids,
                tags=excluded.tags,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                last_event_seq=excluded.last_event_seq
            """,
            (
                belief.id,
                belief.user_id,
                belief.claim,
                belief.domain,
                belief.confidence,
                belief.truth_status.value,
                belief.freshness_status.value,
                belief.lifecycle_status.value,
                belief.protection_level.value,
                belief.successor_id,
                belief.origin,
                _json_dumps(belief._claim_keywords),
                belief.supporting_evidence_count,
                belief.contradicting_evidence_count,
                _json_dumps(belief.source_memory_ids),
                _json_dumps(belief.source_episode_ids),
                _json_dumps(belief.tags),
                belief.created_at,
                belief.updated_at,
                belief.last_event_seq,
            ),
        )

    # ------------------------------------------------------------------
    # Cache hydration (called once on init and on explicit reload)
    # ------------------------------------------------------------------

    def _load(self) -> None:
        with self._get_connection() as conn:
            node_rows = conn.execute(
                "SELECT * FROM belief_nodes ORDER BY updated_at DESC",
            ).fetchall()
            evidence_rows = conn.execute(
                "SELECT * FROM belief_evidence ORDER BY created_at ASC",
            ).fetchall()
            relation_rows = conn.execute(
                "SELECT * FROM belief_relations",
            ).fetchall()
            event_rows = conn.execute(
                "SELECT * FROM belief_events ORDER BY belief_id, seq ASC",
            ).fetchall()

        evidence_by_belief: Dict[str, List[Evidence]] = {}
        for row in evidence_rows:
            evidence_by_belief.setdefault(row["belief_id"], []).append(
                Evidence(
                    id=row["id"],
                    content=row["content"],
                    supports=bool(row["supports"]),
                    source=row["source"],
                    confidence=float(row["confidence"]),
                    timestamp=float(row["created_at"]),
                    memory_id=row["memory_id"],
                    episode_id=row["episode_id"],
                    event_id=row["event_id"],
                )
            )

        contradictions_map: Dict[str, List[str]] = {}
        for row in relation_rows:
            if row["relation_type"] == "contradicts":
                contradictions_map.setdefault(
                    row["belief_a_id"], [],
                ).append(row["belief_b_id"])
                contradictions_map.setdefault(
                    row["belief_b_id"], [],
                ).append(row["belief_a_id"])

        event_map: Dict[str, List[sqlite3.Row]] = {}
        for row in event_rows:
            event_map.setdefault(row["belief_id"], []).append(row)

        existing = self._beliefs
        beliefs: Dict[str, BeliefNode] = {}

        for row in node_rows:
            node = BeliefNode(
                id=row["id"],
                user_id=row["user_id"],
                claim=row["claim"],
                domain=row["domain"],
                confidence=float(row["confidence"]),
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]),
                evidence=evidence_by_belief.get(row["id"], []),
                revisions=[],
                contradicts=contradictions_map.get(row["id"], []),
                source_memory_ids=_json_loads(row["source_memory_ids"], []),
                source_episode_ids=_json_loads(row["source_episode_ids"], []),
                tags=_json_loads(row["tags"], []),
                _claim_keywords=_json_loads(row["claim_keywords"], []),
                truth_status=BeliefStatus(row["truth_status"]),
                freshness_status=BeliefFreshnessStatus(row["freshness_status"]),
                lifecycle_status=BeliefLifecycleStatus(row["lifecycle_status"]),
                protection_level=BeliefProtectionLevel(row["protection_level"]),
                successor_id=row["successor_id"],
                origin=row["origin"],
                last_event_seq=int(row["last_event_seq"] or 0),
            )

            # Reconstruct revisions from events.
            for event in event_map.get(node.id, []):
                old_conf = event["confidence_before"]
                old_status = event["truth_status_before"]
                if old_conf is None or old_status is None:
                    continue
                if (
                    float(old_conf) == float(event["confidence_after"])
                    and str(old_status) == str(event["truth_status_after"])
                ):
                    continue
                node.revisions.append(
                    BeliefRevision(
                        timestamp=float(event["created_at"]),
                        old_confidence=float(old_conf),
                        new_confidence=float(event["confidence_after"]),
                        old_status=str(old_status),
                        new_status=str(event["truth_status_after"]),
                        reason=event["reason"] or event["event_type"],
                        evidence_id=event["evidence_id"],
                    )
                )

            # Preserve object identity for existing references.
            current = existing.get(node.id)
            if current is not None:
                for f in fields(BeliefNode):
                    setattr(current, f.name, getattr(node, f.name))
                beliefs[node.id] = current
            else:
                beliefs[node.id] = node

        self._beliefs = beliefs

    def reload(self) -> None:
        """Re-hydrate the in-memory cache from SQLite."""
        self._load()

    # ------------------------------------------------------------------
    # Public mutation API
    # ------------------------------------------------------------------

    def add_belief(
        self,
        user_id: str,
        claim: str,
        domain: str = "general",
        confidence: float = 0.5,
        source: str = "memory",
        memory_id: Optional[str] = None,
        episode_id: Optional[str] = None,
    ) -> Tuple[BeliefNode, List[BeliefNode]]:
        with self._get_connection() as conn:
            return self._add_belief_impl(
                conn, user_id, claim, domain, confidence,
                source, memory_id, episode_id,
            )

    def _add_belief_impl(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        claim: str,
        domain: str = "general",
        confidence: float = 0.5,
        source: str = "memory",
        memory_id: Optional[str] = None,
        episode_id: Optional[str] = None,
    ) -> Tuple[BeliefNode, List[BeliefNode]]:
        """Core add-belief logic.  Caller must hold ``_get_connection``."""
        keywords = self._extract_keywords(claim)

        existing = self._find_similar(user_id, claim, domain, keywords)
        if existing:
            before_state = self._snapshot(existing)
            evidence = existing.add_evidence(
                content=f"Reinforced: {claim[:200]}",
                supports=True,
                source=source,
                confidence=confidence,
                memory_id=memory_id,
                episode_id=episode_id,
            )
            event_id, _ = self._append_event_conn(
                conn, existing, "reinforced", before_state,
                actor=source,
                reason=f"Reinforced existing belief from '{claim[:100]}'",
                evidence_id=evidence.id,
                payload={"source": source},
            )
            self._insert_evidence_conn(conn, existing, evidence, event_id)
            self._beliefs[existing.id] = existing
            return existing, []

        now = _now()
        truth = BeliefStatus.PROPOSED if confidence < 0.7 else BeliefStatus.HELD
        belief = BeliefNode(
            id=str(uuid.uuid4()),
            user_id=user_id,
            claim=claim,
            domain=domain,
            confidence=confidence,
            created_at=now,
            updated_at=now,
            _claim_keywords=keywords,
            tags=[domain],
            truth_status=truth,
            origin=source,
        )
        if memory_id:
            belief.source_memory_ids.append(memory_id)
        if episode_id:
            belief.source_episode_ids.append(episode_id)

        before_state = self._snapshot(belief)
        evidence = belief.add_evidence(
            content=f"Initial claim: {claim[:200]}",
            supports=True,
            source=source,
            confidence=confidence,
            memory_id=memory_id,
            episode_id=episode_id,
        )

        contradictions = self._detect_contradictions(belief)

        event_id, _ = self._append_event_conn(
            conn, belief, "proposed", before_state,
            actor=source,
            reason="Initial belief creation",
            evidence_id=evidence.id,
            payload={"source": source},
            created_at=now,
        )
        self._insert_evidence_conn(conn, belief, evidence, event_id)

        for contra in contradictions:
            if contra.id not in belief.contradicts:
                belief.contradicts.append(contra.id)
            if belief.id not in contra.contradicts:
                contra.contradicts.append(belief.id)
            contra_before = self._snapshot(contra)
            if contra.truth_status != BeliefStatus.RETRACTED:
                contra.truth_status = BeliefStatus.CHALLENGED
            contra.updated_at = _now()
            self._append_event_conn(
                conn, contra, "challenged", contra_before,
                actor="system",
                reason=(
                    f"Auto-challenged by contradictory belief"
                    f" '{belief.claim[:100]}'"
                ),
                payload={"other_belief_id": belief.id},
            )
            self._upsert_relation_conn(
                conn, belief.id, contra.id, "contradicts", sort_pair=True,
            )
            self._beliefs[contra.id] = contra

        self._upsert_node_conn(conn, belief)
        self._beliefs[belief.id] = belief
        return belief, contradictions

    def challenge_belief(
        self,
        belief_id: str,
        contradicting_content: str,
        source: str = "observation",
        confidence: float = 0.5,
        memory_id: Optional[str] = None,
    ) -> Optional[BeliefNode]:
        with self._get_connection() as conn:
            belief = self._beliefs.get(belief_id)
            if not belief:
                return None
            before_state = self._snapshot(belief)
            evidence = belief.add_evidence(
                content=contradicting_content,
                supports=False,
                source=source,
                confidence=confidence,
                memory_id=memory_id,
            )
            event_id, _ = self._append_event_conn(
                conn, belief, "challenged", before_state,
                actor=source,
                reason=contradicting_content[:200],
                evidence_id=evidence.id,
                payload={"source": source},
            )
            self._insert_evidence_conn(conn, belief, evidence, event_id)
            self._beliefs[belief.id] = belief
            return belief

    def reinforce_belief(
        self,
        belief_id: str,
        supporting_content: str,
        source: str = "observation",
        confidence: float = 0.5,
        memory_id: Optional[str] = None,
    ) -> Optional[BeliefNode]:
        with self._get_connection() as conn:
            belief = self._beliefs.get(belief_id)
            if not belief:
                return None
            before_state = self._snapshot(belief)
            evidence = belief.add_evidence(
                content=supporting_content,
                supports=True,
                source=source,
                confidence=confidence,
                memory_id=memory_id,
            )
            event_id, _ = self._append_event_conn(
                conn, belief, "reinforced", before_state,
                actor=source,
                reason=supporting_content[:200],
                evidence_id=evidence.id,
                payload={"source": source},
            )
            self._insert_evidence_conn(conn, belief, evidence, event_id)
            self._beliefs[belief.id] = belief
            return belief

    def mark_stale(
        self,
        belief_id: str,
        reason: str = "",
        actor: str = "user",
    ) -> Optional[BeliefNode]:
        with self._get_connection() as conn:
            belief = self._beliefs.get(belief_id)
            if not belief:
                return None
            before_state = self._snapshot(belief)
            belief.freshness_status = BeliefFreshnessStatus.STALE
            belief.updated_at = _now()
            self._append_event_conn(
                conn, belief, "marked_stale", before_state,
                actor=actor, reason=reason or "Marked stale",
            )
            self._beliefs[belief.id] = belief
            return belief

    def correct_belief(
        self,
        belief_id: str,
        new_claim: str,
        reason: str = "",
        actor: str = "user",
    ) -> Optional[Tuple[BeliefNode, BeliefNode]]:
        with self._get_connection() as conn:
            old_belief = self._beliefs.get(belief_id)
            if not old_belief:
                return None

            # Create successor via the internal path (same conn, same txn).
            new_belief, _ = self._add_belief_impl(
                conn,
                user_id=old_belief.user_id,
                claim=new_claim,
                domain=old_belief.domain,
                confidence=0.5,
                source="correction",
            )

            old_before = self._snapshot(old_belief)
            old_belief.freshness_status = BeliefFreshnessStatus.SUPERSEDED
            old_belief.successor_id = new_belief.id
            old_belief.updated_at = _now()
            self._append_event_conn(
                conn, old_belief, "corrected", old_before,
                actor=actor,
                reason=reason or f"Corrected to '{new_claim[:120]}'",
                successor_belief_id=new_belief.id,
                payload={"new_claim": new_claim},
            )
            self._upsert_relation_conn(
                conn, old_belief.id, new_belief.id, "supersedes",
            )
            self._beliefs[old_belief.id] = old_belief
            return old_belief, new_belief

    def tombstone_belief(
        self,
        belief_id: str,
        reason: str = "",
        actor: str = "user",
    ) -> Optional[BeliefNode]:
        with self._get_connection() as conn:
            belief = self._beliefs.get(belief_id)
            if not belief:
                return None
            before_state = self._snapshot(belief)
            belief.lifecycle_status = BeliefLifecycleStatus.TOMBSTONED
            belief.updated_at = _now()
            self._append_event_conn(
                conn, belief, "tombstoned", before_state,
                actor=actor, reason=reason or "Tombstoned",
            )
            self._beliefs[belief.id] = belief
            return belief

    def pin_belief(
        self,
        belief_id: str,
        pinned: bool = True,
        reason: str = "",
        actor: str = "user",
    ) -> Optional[BeliefNode]:
        with self._get_connection() as conn:
            belief = self._beliefs.get(belief_id)
            if not belief:
                return None
            before_state = self._snapshot(belief)
            belief.protection_level = (
                BeliefProtectionLevel.PINNED
                if pinned
                else BeliefProtectionLevel.NORMAL
            )
            belief.updated_at = _now()
            self._append_event_conn(
                conn, belief,
                "pinned" if pinned else "unpinned",
                before_state,
                actor=actor,
                reason=reason or ("Pinned" if pinned else "Unpinned"),
            )
            self._beliefs[belief.id] = belief
            return belief

    def merge_beliefs(
        self,
        survivor_id: str,
        loser_id: str,
        reason: str = "",
        actor: str = "user",
    ) -> Optional[BeliefNode]:
        with self._get_connection() as conn:
            survivor = self._beliefs.get(survivor_id)
            loser = self._beliefs.get(loser_id)
            if not survivor or not loser or survivor.id == loser.id:
                return None

            survivor_before = self._snapshot(survivor)
            loser_before = self._snapshot(loser)

            # Copy evidence from loser into survivor.
            copied: List[Evidence] = []
            existing_ids = {e.id for e in survivor.evidence}
            for item in loser.evidence:
                if item.id in existing_ids:
                    continue
                copied.append(
                    Evidence(
                        id=str(uuid.uuid4()),
                        content=item.content,
                        supports=item.supports,
                        source=item.source,
                        confidence=item.confidence,
                        timestamp=_now(),
                        memory_id=item.memory_id,
                        episode_id=item.episode_id,
                    )
                )

            survivor.source_memory_ids = list(dict.fromkeys(
                survivor.source_memory_ids + loser.source_memory_ids,
            ))
            survivor.source_episode_ids = list(dict.fromkeys(
                survivor.source_episode_ids + loser.source_episode_ids,
            ))
            survivor.tags = list(dict.fromkeys(survivor.tags + loser.tags))
            survivor.updated_at = _now()

            loser.lifecycle_status = BeliefLifecycleStatus.ARCHIVED
            loser.freshness_status = BeliefFreshnessStatus.SUPERSEDED
            loser.successor_id = survivor.id
            loser.updated_at = _now()

            survivor_eid, _ = self._append_event_conn(
                conn, survivor, "merged", survivor_before,
                actor=actor,
                reason=(
                    reason
                    or f"Merged belief '{loser.claim[:100]}' into survivor"
                ),
                payload={
                    "loser_belief_id": loser.id,
                    "copied_evidence": len(copied),
                },
            )
            for ev in copied:
                survivor.evidence.append(ev)
                self._insert_evidence_conn(conn, survivor, ev, survivor_eid)
            self._upsert_node_conn(conn, survivor)

            self._append_event_conn(
                conn, loser, "merged", loser_before,
                actor=actor,
                reason=(
                    reason
                    or f"Merged into belief '{survivor.claim[:100]}'"
                ),
                successor_belief_id=survivor.id,
                payload={"survivor_belief_id": survivor.id},
            )
            self._upsert_relation_conn(
                conn, loser.id, survivor.id, "merged_into",
            )

            self._beliefs[survivor.id] = survivor
            self._beliefs[loser.id] = loser
            return survivor

    # ------------------------------------------------------------------
    # Public read / query API
    # ------------------------------------------------------------------

    def get_belief(
        self, belief_id: str, include_inactive: bool = True,
    ) -> Optional[BeliefNode]:
        belief = self._beliefs.get(belief_id)
        if not belief:
            return None
        if not include_inactive and not belief.is_listable():
            return None
        return belief

    def get_beliefs(
        self,
        user_id: str,
        domain: Optional[str] = None,
        min_confidence: float = 0.0,
        include_retracted: bool = False,
        limit: int = 20,
        truth_status: Optional[str] = None,
        freshness_status: Optional[str] = None,
        lifecycle_status: Optional[str] = None,
        protection_level: Optional[str] = None,
        origin: Optional[str] = None,
    ) -> List[BeliefNode]:
        result: List[BeliefNode] = []
        for belief in self._beliefs.values():
            if belief.user_id != user_id:
                continue
            if domain and belief.domain != domain:
                continue
            if belief.confidence < min_confidence:
                continue
            if belief.truth_status == BeliefStatus.RETRACTED and not include_retracted:
                continue
            # Lifecycle filter
            if lifecycle_status:
                if belief.lifecycle_status.value != lifecycle_status:
                    continue
            elif belief.lifecycle_status != BeliefLifecycleStatus.ACTIVE:
                continue
            # Freshness filter — hide superseded unless explicitly requested
            if freshness_status:
                if belief.freshness_status.value != freshness_status:
                    continue
            elif belief.freshness_status == BeliefFreshnessStatus.SUPERSEDED:
                continue
            if truth_status and belief.truth_status.value != truth_status:
                continue
            if protection_level and belief.protection_level.value != protection_level:
                continue
            if origin and belief.origin != origin:
                continue
            result.append(belief)
        result.sort(key=lambda b: b.confidence, reverse=True)
        return result[:limit]

    def query_beliefs(
        self,
        *,
        user_id: Optional[str] = None,
        search: Optional[str] = None,
        domain: Optional[str] = None,
        truth_status: Optional[str] = None,
        freshness_status: Optional[str] = None,
        lifecycle_status: Optional[str] = None,
        protection_level: Optional[str] = None,
        origin: Optional[str] = None,
        min_confidence: float = 0.0,
        max_confidence: float = 1.0,
        page: int = 1,
        page_size: int = 50,
        include_inactive: bool = True,
    ) -> Tuple[List[BeliefNode], int]:
        query_words = (
            set(self._extract_keywords(search)) if search else set()
        )
        filtered: List[BeliefNode] = []
        for belief in self._beliefs.values():
            if user_id and belief.user_id != user_id:
                continue
            if domain and belief.domain != domain:
                continue
            if not include_inactive and not belief.is_listable():
                continue
            if truth_status and belief.truth_status.value != truth_status:
                continue
            # Freshness filter — hide superseded unless explicitly requested
            if freshness_status:
                if belief.freshness_status.value != freshness_status:
                    continue
            elif belief.freshness_status == BeliefFreshnessStatus.SUPERSEDED:
                continue
            if lifecycle_status and belief.lifecycle_status.value != lifecycle_status:
                continue
            if protection_level and belief.protection_level.value != protection_level:
                continue
            if origin and belief.origin != origin:
                continue
            if belief.confidence < min_confidence or belief.confidence > max_confidence:
                continue
            if query_words and not (query_words & set(belief._claim_keywords)):
                if search and search.lower() not in belief.claim.lower():
                    continue
            filtered.append(belief)
        filtered.sort(key=lambda b: b.updated_at, reverse=True)
        total = len(filtered)
        ps = max(page_size, 1)
        start = max(0, (max(page, 1) - 1) * ps)
        return filtered[start : start + ps], total

    def get_relevant_beliefs(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> List[BeliefNode]:
        query_words = set(self._extract_keywords(query))
        if not query_words:
            return []

        scored: List[Tuple[BeliefNode, float]] = []
        for belief in self._beliefs.values():
            if belief.user_id != user_id:
                continue
            if belief.lifecycle_status != BeliefLifecycleStatus.ACTIVE:
                continue
            if belief.truth_status == BeliefStatus.RETRACTED:
                continue
            if belief.freshness_status == BeliefFreshnessStatus.SUPERSEDED:
                continue

            weight = 1.0
            if belief.freshness_status == BeliefFreshnessStatus.STALE:
                weight *= 0.7
            if belief.protection_level == BeliefProtectionLevel.PINNED:
                weight *= 1.05

            overlap = len(query_words & set(belief._claim_keywords))
            if overlap > 0:
                score = overlap * belief.confidence * belief.stability * weight
                scored.append((belief, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [b for b, _ in scored[:limit]]

    def get_contradictions(
        self, user_id: str,
    ) -> List[Tuple[BeliefNode, BeliefNode]]:
        pairs: List[Tuple[BeliefNode, BeliefNode]] = []
        seen: set = set()
        for belief in self._beliefs.values():
            if belief.user_id != user_id:
                continue
            if belief.lifecycle_status != BeliefLifecycleStatus.ACTIVE:
                continue
            if belief.truth_status == BeliefStatus.RETRACTED:
                continue
            if belief.freshness_status == BeliefFreshnessStatus.SUPERSEDED:
                continue
            for contra_id in belief.contradicts:
                pair_key = tuple(sorted([belief.id, contra_id]))
                if pair_key in seen:
                    continue
                contra = self._beliefs.get(contra_id)
                if not contra:
                    continue
                if contra.user_id != user_id:
                    continue
                if contra.lifecycle_status != BeliefLifecycleStatus.ACTIVE:
                    continue
                if contra.truth_status == BeliefStatus.RETRACTED:
                    continue
                if contra.freshness_status == BeliefFreshnessStatus.SUPERSEDED:
                    continue
                seen.add(pair_key)
                pairs.append((belief, contra))
        return pairs

    def get_belief_history(
        self, belief_id: str, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM belief_events"
                " WHERE belief_id = ? ORDER BY seq DESC LIMIT ?",
                (belief_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json_loads(item.pop("payload"), {})
            result.append(item)
        return result

    def get_belief_evidence(
        self, belief_id: str, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM belief_evidence"
                " WHERE belief_id = ? ORDER BY created_at DESC LIMIT ?",
                (belief_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Influence tracking
    # ------------------------------------------------------------------

    def record_influence(
        self,
        belief_id: str,
        user_id: str,
        influence_type: str,
        query: Optional[str] = None,
        session_id: Optional[str] = None,
        answer_fragment: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO belief_influence_events (
                    id, belief_id, user_id, influence_type, query,
                    session_id, answer_fragment, metadata_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(uuid.uuid4()),
                    belief_id,
                    user_id,
                    influence_type,
                    query,
                    session_id,
                    answer_fragment,
                    _json_dumps(metadata or {}),
                    _now(),
                ),
            )

    def get_influence_history(
        self, belief_id: str, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM belief_influence_events"
                " WHERE belief_id = ? ORDER BY created_at DESC LIMIT ?",
                (belief_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _json_loads(item.pop("metadata_json"), {})
            result.append(item)
        return result

    def get_belief_impact(
        self, belief_id: str, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.get_influence_history(belief_id, limit=limit)

    def get_influence_stats(self, user_id: str) -> Dict[str, Any]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT influence_type, COUNT(*) AS count"
                " FROM belief_influence_events"
                " WHERE user_id = ?"
                " GROUP BY influence_type",
                (user_id,),
            ).fetchall()
        return {
            "total": sum(int(row["count"]) for row in rows),
            "by_type": {
                row["influence_type"]: int(row["count"]) for row in rows
            },
        }

    def list_activity(
        self,
        *,
        user_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            # Belief events — filter via JOIN so deleted beliefs don't break.
            if user_id:
                event_rows = conn.execute(
                    """
                    SELECT 'belief_event' AS row_type,
                           e.id, e.belief_id, e.event_type AS kind,
                           e.actor, e.reason, e.created_at
                    FROM belief_events e
                    JOIN belief_nodes n ON n.id = e.belief_id
                    WHERE n.user_id = ?
                    ORDER BY e.created_at DESC LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
            else:
                event_rows = conn.execute(
                    """
                    SELECT 'belief_event' AS row_type,
                           id, belief_id, event_type AS kind,
                           actor, reason, created_at
                    FROM belief_events
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            influence_rows = conn.execute(
                """
                SELECT 'influence_event' AS row_type,
                       id, belief_id, influence_type AS kind,
                       user_id AS actor, query AS reason, created_at
                FROM belief_influence_events
                WHERE (? IS NULL OR user_id = ?)
                ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, user_id, limit),
            ).fetchall()

        combined = [dict(r) for r in event_rows] + [dict(r) for r in influence_rows]
        combined.sort(key=lambda x: x["created_at"], reverse=True)
        return combined[:limit]

    def list_user_ids(self) -> List[str]:
        return sorted({b.user_id for b in self._beliefs.values()})

    # ------------------------------------------------------------------
    # Pruning / stats
    # ------------------------------------------------------------------

    def prune_retracted(self, user_id: str, max_age_days: int = 30) -> int:
        cutoff = _now() - max_age_days * 86400
        archived = 0
        with self._get_connection() as conn:
            for belief in list(self._beliefs.values()):
                if belief.user_id != user_id:
                    continue
                if belief.truth_status != BeliefStatus.RETRACTED:
                    continue
                if belief.updated_at >= cutoff:
                    continue
                if belief.protection_level == BeliefProtectionLevel.PINNED:
                    continue
                if belief.lifecycle_status != BeliefLifecycleStatus.ACTIVE:
                    continue
                before_state = self._snapshot(belief)
                belief.lifecycle_status = BeliefLifecycleStatus.ARCHIVED
                belief.updated_at = _now()
                self._append_event_conn(
                    conn, belief, "archived", before_state,
                    actor="system",
                    reason="Auto-archived retracted belief",
                )
                self._beliefs[belief.id] = belief
                archived += 1
        return archived

    def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        beliefs = list(self._beliefs.values())
        if user_id:
            beliefs = [b for b in beliefs if b.user_id == user_id]
        by_status: Dict[str, int] = {}
        for b in beliefs:
            key = b.truth_status.value
            by_status[key] = by_status.get(key, 0) + 1
        return {
            "total": len(beliefs),
            "by_status": by_status,
            "avg_confidence": (
                sum(b.confidence for b in beliefs) / len(beliefs)
                if beliefs
                else 0.0
            ),
            "contradictions": sum(1 for b in beliefs if b.contradicts),
            "stale": sum(
                1
                for b in beliefs
                if b.freshness_status == BeliefFreshnessStatus.STALE
            ),
            "pinned": sum(
                1
                for b in beliefs
                if b.protection_level == BeliefProtectionLevel.PINNED
            ),
            "tombstoned": sum(
                1
                for b in beliefs
                if b.lifecycle_status == BeliefLifecycleStatus.TOMBSTONED
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_contradictions(
        self, new_belief: BeliefNode,
    ) -> List[BeliefNode]:
        contradictions: List[BeliefNode] = []
        new_words = set(new_belief._claim_keywords)
        if len(new_words) < 2:
            return []
        new_claim_lower = new_belief.claim.lower()
        for existing in self._beliefs.values():
            if existing.user_id != new_belief.user_id:
                continue
            if existing.id == new_belief.id:
                continue
            if existing.domain != new_belief.domain:
                continue
            if existing.lifecycle_status != BeliefLifecycleStatus.ACTIVE:
                continue
            if existing.truth_status == BeliefStatus.RETRACTED:
                continue
            ex_words = set(existing._claim_keywords)
            if not ex_words:
                continue
            jaccard = len(new_words & ex_words) / len(new_words | ex_words)
            if jaccard < self.CONTRADICTION_THRESHOLD:
                continue
            if self._has_negation_pattern(new_claim_lower, existing.claim.lower()):
                contradictions.append(existing)
        return contradictions

    @staticmethod
    def _has_negation_pattern(claim_a: str, claim_b: str) -> bool:
        negation_words = {
            "not", "no", "never", "neither", "cannot", "can't", "don't",
            "doesn't", "didn't", "won't", "isn't", "aren't", "wasn't",
            "weren't", "shouldn't", "wouldn't",
        }
        words_a = set(claim_a.split())
        words_b = set(claim_b.split())
        neg_a = bool(words_a & negation_words)
        neg_b = bool(words_b & negation_words)
        if neg_a != neg_b:
            return True
        opposites = [
            ("true", "false"), ("yes", "no"), ("always", "never"),
            ("correct", "incorrect"), ("valid", "invalid"),
            ("should", "shouldn't"), ("can", "cannot"),
            ("works", "broken"), ("enabled", "disabled"),
            ("supports", "lacks"), ("fast", "slow"),
            ("better", "worse"), ("increase", "decrease"),
        ]
        for pos, neg in opposites:
            if (pos in claim_a and neg in claim_b) or (
                neg in claim_a and pos in claim_b
            ):
                return True
        return False

    def _find_similar(
        self,
        user_id: str,
        claim: str,
        domain: str,
        keywords: List[str],
    ) -> Optional[BeliefNode]:
        kw_set = set(keywords)
        if len(kw_set) < 2:
            return None
        for belief in self._beliefs.values():
            if belief.user_id != user_id or belief.domain != domain:
                continue
            if belief.lifecycle_status != BeliefLifecycleStatus.ACTIVE:
                continue
            if belief.truth_status == BeliefStatus.RETRACTED:
                continue
            b_words = set(belief._claim_keywords)
            if not b_words:
                continue
            overlap = len(kw_set & b_words) / len(kw_set | b_words)
            if overlap > 0.7:
                return belief
        return None

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        stop = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "can", "to", "of", "in",
            "for", "on", "with", "at", "by", "from", "as", "into",
            "and", "or", "but", "if", "it", "its", "this", "that",
        }
        words = text.lower().split()
        return [w for w in words if len(w) > 2 and w not in stop][:20]

    def flush(self) -> None:
        """Persist any in-memory state to SQLite.

        With the event-sourced architecture, all mutations already commit
        transactionally.  This method exists for interface compatibility
        and ensures the node projection is fully up to date.
        """
        with self._get_connection() as conn:
            for belief in self._beliefs.values():
                self._upsert_node_conn(conn, belief)
