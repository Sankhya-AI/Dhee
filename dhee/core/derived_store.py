"""Dhee v3 — Type-specific derived cognition stores.

Each derived type gets its own store class because they have different:
- Lifecycle rules (beliefs: Bayesian; policies: win-rate; insights: strength)
- Indexing needs (anchors: era/place; policies: granularity/utility)
- Invalidation behavior (beliefs: retract; policies: deprecate; anchors: re-resolve)
- Conflict semantics (beliefs: contradiction pairs; policies: approach conflicts)

All stores share a common database connection (from RawEventStore) and
the derived_lineage table for traceability.

Design contract:
    - Every derived object has derivation_version + lineage_fingerprint
    - Invalidation statuses (stale, suspect, invalidated) are orthogonal to
      type-specific lifecycle statuses
    - Zero LLM calls — pure storage and state transitions
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default if default is not None else []
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def _compute_lineage_fingerprint(source_event_ids: List[str], version: int) -> str:
    """Deterministic fingerprint from sorted source IDs + version."""
    payload = "|".join(sorted(source_event_ids)) + f"|v{version}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# =========================================================================
# Enums
# =========================================================================

class BeliefStatus(str, Enum):
    PROPOSED = "proposed"
    HELD = "held"
    CHALLENGED = "challenged"
    REVISED = "revised"
    RETRACTED = "retracted"
    # Invalidation statuses (from three-tier model)
    STALE = "stale"
    SUSPECT = "suspect"
    INVALIDATED = "invalidated"


class PolicyStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    VALIDATED = "validated"
    DEPRECATED = "deprecated"
    STALE = "stale"
    SUSPECT = "suspect"
    INVALIDATED = "invalidated"


class PolicyGranularity(str, Enum):
    TASK = "task"
    STEP = "step"


class InsightType(str, Enum):
    CAUSAL = "causal"
    WARNING = "warning"
    STRATEGY = "strategy"
    PATTERN = "pattern"


class AbstractionLevel(str, Enum):
    SPECIFIC = "specific"
    DOMAIN = "domain"
    UNIVERSAL = "universal"


class DerivedType(str, Enum):
    BELIEF = "belief"
    POLICY = "policy"
    ANCHOR = "anchor"
    INSIGHT = "insight"
    HEURISTIC = "heuristic"


class DerivedStatus(str, Enum):
    """Common invalidation statuses across all derived types."""
    ACTIVE = "active"
    STALE = "stale"
    SUSPECT = "suspect"
    INVALIDATED = "invalidated"


# =========================================================================
# Base store with shared connection management
# =========================================================================

class _DerivedStoreBase:
    """Shared connection management for all derived stores.

    All stores share a single SQLite connection. The connection is
    created externally (by RawEventStore or a coordinator) and passed in.
    """

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


# =========================================================================
# BeliefStore
# =========================================================================

class BeliefStore(_DerivedStoreBase):
    """Confidence-tracked claims with Bayesian updates and contradiction detection."""

    def add(
        self,
        user_id: str,
        claim: str,
        *,
        domain: str = "general",
        confidence: float = 0.5,
        source_memory_ids: Optional[List[str]] = None,
        source_episode_ids: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        belief_id: Optional[str] = None,
    ) -> str:
        bid = belief_id or str(uuid.uuid4())
        now = _utcnow_iso()
        smids = source_memory_ids or []
        seids = source_episode_ids or []
        fp = _compute_lineage_fingerprint(smids, 1)

        with self._tx() as conn:
            conn.execute(
                """INSERT INTO beliefs
                   (belief_id, user_id, claim, domain, status, confidence,
                    source_memory_ids, source_episode_ids, derivation_version,
                    lineage_fingerprint, created_at, updated_at, tags_json)
                   VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, 1, ?, ?, ?, ?)""",
                (
                    bid, user_id, claim, domain, confidence,
                    json.dumps(smids), json.dumps(seids), fp,
                    now, now, json.dumps(tags or []),
                ),
            )
        return bid

    def get(self, belief_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM beliefs WHERE belief_id = ?",
                (belief_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_user(
        self,
        user_id: str,
        *,
        domain: Optional[str] = None,
        status: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM beliefs WHERE user_id = ? AND confidence >= ?"
        params: list = [user_id, min_confidence]
        if domain:
            query += " AND domain = ?"
            params.append(domain)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY confidence DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_confidence(
        self,
        belief_id: str,
        new_confidence: float,
        *,
        new_status: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        revision_reason: Optional[str] = None,
    ) -> bool:
        """Update belief confidence with optional evidence and revision tracking."""
        now = _utcnow_iso()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT confidence, status, evidence_json, revisions_json FROM beliefs WHERE belief_id = ?",
                (belief_id,),
            ).fetchone()
            if not row:
                return False

            old_conf = row["confidence"]
            old_status = row["status"]

            # Append evidence
            evidence_list = _parse_json(row["evidence_json"], [])
            if evidence:
                evidence_list.append(evidence)

            # Append revision
            revisions = _parse_json(row["revisions_json"], [])
            revisions.append({
                "timestamp": now,
                "old_confidence": old_conf,
                "new_confidence": new_confidence,
                "old_status": old_status,
                "new_status": new_status or old_status,
                "reason": revision_reason or "confidence_update",
            })

            # Auto-derive status if not explicitly set
            status = new_status
            if not status:
                if new_confidence >= 0.7:
                    status = BeliefStatus.HELD.value
                elif new_confidence <= 0.1:
                    status = BeliefStatus.RETRACTED.value
                else:
                    status = old_status

            conn.execute(
                """UPDATE beliefs
                   SET confidence = ?, status = ?, evidence_json = ?,
                       revisions_json = ?, updated_at = ?
                   WHERE belief_id = ?""",
                (
                    new_confidence, status, json.dumps(evidence_list),
                    json.dumps(revisions), now, belief_id,
                ),
            )
        return True

    def add_contradiction(self, belief_a_id: str, belief_b_id: str) -> None:
        """Link two beliefs as contradicting each other."""
        now = _utcnow_iso()
        with self._tx() as conn:
            for bid, other_id in [(belief_a_id, belief_b_id), (belief_b_id, belief_a_id)]:
                row = conn.execute(
                    "SELECT contradicts_ids FROM beliefs WHERE belief_id = ?",
                    (bid,),
                ).fetchone()
                if row:
                    ids = _parse_json(row["contradicts_ids"], [])
                    if other_id not in ids:
                        ids.append(other_id)
                        conn.execute(
                            "UPDATE beliefs SET contradicts_ids = ?, status = 'challenged', updated_at = ? WHERE belief_id = ?",
                            (json.dumps(ids), now, bid),
                        )

    def set_status(self, belief_id: str, status: str) -> bool:
        with self._tx() as conn:
            result = conn.execute(
                "UPDATE beliefs SET status = ?, updated_at = ? WHERE belief_id = ?",
                (status, _utcnow_iso(), belief_id),
            )
        return result.rowcount > 0

    def get_by_invalidation_status(
        self, status: str, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get beliefs in stale/suspect/invalidated status for repair jobs."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM beliefs WHERE status = ? LIMIT ?",
                (status, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "belief_id": row["belief_id"],
            "user_id": row["user_id"],
            "claim": row["claim"],
            "domain": row["domain"],
            "status": row["status"],
            "confidence": row["confidence"],
            "evidence": _parse_json(row["evidence_json"], []),
            "revisions": _parse_json(row["revisions_json"], []),
            "contradicts_ids": _parse_json(row["contradicts_ids"], []),
            "source_memory_ids": _parse_json(row["source_memory_ids"], []),
            "source_episode_ids": _parse_json(row["source_episode_ids"], []),
            "derivation_version": row["derivation_version"],
            "lineage_fingerprint": row["lineage_fingerprint"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "tags": _parse_json(row["tags_json"], []),
        }


# =========================================================================
# PolicyStore
# =========================================================================

class PolicyStore(_DerivedStoreBase):
    """Condition→action rules with utility tracking (D2Skill dual-granularity)."""

    def add(
        self,
        user_id: str,
        name: str,
        condition: Dict[str, Any],
        action: Dict[str, Any],
        *,
        granularity: str = "task",
        source_task_ids: Optional[List[str]] = None,
        source_episode_ids: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        policy_id: Optional[str] = None,
    ) -> str:
        pid = policy_id or str(uuid.uuid4())
        now = _utcnow_iso()
        stids = source_task_ids or []
        seids = source_episode_ids or []
        fp = _compute_lineage_fingerprint(stids, 1)

        with self._tx() as conn:
            conn.execute(
                """INSERT INTO policies
                   (policy_id, user_id, name, granularity, status,
                    condition_json, action_json, source_task_ids,
                    source_episode_ids, derivation_version,
                    lineage_fingerprint, created_at, updated_at, tags_json)
                   VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
                (
                    pid, user_id, name, granularity,
                    json.dumps(condition), json.dumps(action),
                    json.dumps(stids), json.dumps(seids), fp,
                    now, now, json.dumps(tags or []),
                ),
            )
        return pid

    def get(self, policy_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM policies WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_user(
        self,
        user_id: str,
        *,
        granularity: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM policies WHERE user_id = ?"
        params: list = [user_id]
        if granularity:
            query += " AND granularity = ?"
            params.append(granularity)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY utility DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def record_outcome(
        self,
        policy_id: str,
        success: bool,
        *,
        baseline_score: Optional[float] = None,
        actual_score: Optional[float] = None,
    ) -> Optional[float]:
        """Record an outcome for a policy. Returns the delta if scores provided.

        Updates apply_count, success/failure counts, and utility EMA.
        Auto-transitions status: validated (win_rate >= 0.6 after 5+) or
        deprecated (win_rate < 0.4 after 5+).
        """
        now = _utcnow_iso()
        with self._tx() as conn:
            row = conn.execute(
                """SELECT apply_count, success_count, failure_count,
                          utility, cumulative_delta, status
                   FROM policies WHERE policy_id = ?""",
                (policy_id,),
            ).fetchone()
            if not row:
                return None

            apply_count = row["apply_count"] + 1
            success_count = row["success_count"] + (1 if success else 0)
            failure_count = row["failure_count"] + (0 if success else 1)
            utility = row["utility"]
            cumulative = row["cumulative_delta"]
            status = row["status"]

            delta = 0.0
            if baseline_score is not None and actual_score is not None:
                delta = actual_score - baseline_score
                utility = 0.3 * delta + 0.7 * utility  # EMA alpha=0.3
                cumulative += delta

            # Auto-transition after enough data
            if apply_count >= 5 and status not in ("stale", "suspect", "invalidated"):
                win_rate = (success_count + 1) / (apply_count + 2)  # Laplace
                if win_rate >= 0.6:
                    status = PolicyStatus.VALIDATED.value
                elif win_rate < 0.4:
                    status = PolicyStatus.DEPRECATED.value
                elif status == PolicyStatus.PROPOSED.value:
                    status = PolicyStatus.ACTIVE.value

            conn.execute(
                """UPDATE policies
                   SET apply_count = ?, success_count = ?, failure_count = ?,
                       utility = ?, last_delta = ?, cumulative_delta = ?,
                       status = ?, updated_at = ?
                   WHERE policy_id = ?""",
                (
                    apply_count, success_count, failure_count,
                    utility, delta, cumulative, status, now, policy_id,
                ),
            )
        return delta

    def set_status(self, policy_id: str, status: str) -> bool:
        with self._tx() as conn:
            result = conn.execute(
                "UPDATE policies SET status = ?, updated_at = ? WHERE policy_id = ?",
                (status, _utcnow_iso(), policy_id),
            )
        return result.rowcount > 0

    def get_by_invalidation_status(
        self, status: str, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM policies WHERE status = ? LIMIT ?",
                (status, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "policy_id": row["policy_id"],
            "user_id": row["user_id"],
            "name": row["name"],
            "granularity": row["granularity"],
            "status": row["status"],
            "condition": _parse_json(row["condition_json"], {}),
            "action": _parse_json(row["action_json"], {}),
            "apply_count": row["apply_count"],
            "success_count": row["success_count"],
            "failure_count": row["failure_count"],
            "utility": row["utility"],
            "last_delta": row["last_delta"],
            "cumulative_delta": row["cumulative_delta"],
            "source_task_ids": _parse_json(row["source_task_ids"], []),
            "source_episode_ids": _parse_json(row["source_episode_ids"], []),
            "derivation_version": row["derivation_version"],
            "lineage_fingerprint": row["lineage_fingerprint"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "tags": _parse_json(row["tags_json"], []),
        }


# =========================================================================
# AnchorStore
# =========================================================================

class AnchorStore(_DerivedStoreBase):
    """Hierarchical context anchors (era/place/time/activity)."""

    def add(
        self,
        user_id: str,
        *,
        memory_event_id: Optional[str] = None,
        era: Optional[str] = None,
        place: Optional[str] = None,
        place_type: Optional[str] = None,
        place_detail: Optional[str] = None,
        time_absolute: Optional[str] = None,
        time_markers: Optional[List[str]] = None,
        time_range_start: Optional[str] = None,
        time_range_end: Optional[str] = None,
        time_derivation: Optional[str] = None,
        activity: Optional[str] = None,
        session_id: Optional[str] = None,
        session_position: int = 0,
        anchor_id: Optional[str] = None,
    ) -> str:
        aid = anchor_id or str(uuid.uuid4())
        now = _utcnow_iso()
        source_ids = [memory_event_id] if memory_event_id else []
        fp = _compute_lineage_fingerprint(source_ids, 1)

        with self._tx() as conn:
            conn.execute(
                """INSERT INTO anchors
                   (anchor_id, user_id, memory_event_id, era, place,
                    place_type, place_detail, time_absolute,
                    time_markers_json, time_range_start, time_range_end,
                    time_derivation, activity, session_id, session_position,
                    derivation_version, lineage_fingerprint, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    aid, user_id, memory_event_id, era, place,
                    place_type, place_detail, time_absolute,
                    json.dumps(time_markers or []),
                    time_range_start, time_range_end, time_derivation,
                    activity, session_id, session_position, fp, now,
                ),
            )
        return aid

    def get(self, anchor_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM anchors WHERE anchor_id = ?",
                (anchor_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_by_event(self, memory_event_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM anchors WHERE memory_event_id = ?",
                (memory_event_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_user(
        self,
        user_id: str,
        *,
        era: Optional[str] = None,
        place: Optional[str] = None,
        activity: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM anchors WHERE user_id = ?"
        params: list = [user_id]
        if era:
            query += " AND era = ?"
            params.append(era)
        if place:
            query += " AND place = ?"
            params.append(place)
        if activity:
            query += " AND activity = ?"
            params.append(activity)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_fields(self, anchor_id: str, **fields: Any) -> bool:
        """Update specific anchor fields. Only allows known anchor columns."""
        allowed = {
            "era", "place", "place_type", "place_detail",
            "time_absolute", "time_markers_json", "time_range_start",
            "time_range_end", "time_derivation", "activity",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [anchor_id]

        with self._tx() as conn:
            result = conn.execute(
                f"UPDATE anchors SET {set_clause} WHERE anchor_id = ?",
                values,
            )
        return result.rowcount > 0

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "anchor_id": row["anchor_id"],
            "user_id": row["user_id"],
            "memory_event_id": row["memory_event_id"],
            "era": row["era"],
            "place": row["place"],
            "place_type": row["place_type"],
            "place_detail": row["place_detail"],
            "time_absolute": row["time_absolute"],
            "time_markers": _parse_json(row["time_markers_json"], []),
            "time_range_start": row["time_range_start"],
            "time_range_end": row["time_range_end"],
            "time_derivation": row["time_derivation"],
            "activity": row["activity"],
            "session_id": row["session_id"],
            "session_position": row["session_position"],
            "derivation_version": row["derivation_version"],
            "lineage_fingerprint": row["lineage_fingerprint"],
            "created_at": row["created_at"],
        }


# =========================================================================
# InsightStore
# =========================================================================

class InsightStore(_DerivedStoreBase):
    """Synthesized causal hypotheses with strength tracking."""

    def add(
        self,
        user_id: str,
        content: str,
        *,
        insight_type: str = "pattern",
        source_task_types: Optional[List[str]] = None,
        confidence: float = 0.5,
        tags: Optional[List[str]] = None,
        insight_id: Optional[str] = None,
    ) -> str:
        iid = insight_id or str(uuid.uuid4())
        now = _utcnow_iso()

        with self._tx() as conn:
            conn.execute(
                """INSERT INTO insights
                   (insight_id, user_id, content, insight_type,
                    source_task_types_json, confidence,
                    derivation_version, lineage_fingerprint,
                    created_at, tags_json)
                   VALUES (?, ?, ?, ?, ?, ?, 1, '', ?, ?)""",
                (
                    iid, user_id, content, insight_type,
                    json.dumps(source_task_types or []),
                    confidence, now, json.dumps(tags or []),
                ),
            )
        return iid

    def get(self, insight_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM insights WHERE insight_id = ?",
                (insight_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_user(
        self,
        user_id: str,
        *,
        insight_type: Optional[str] = None,
        min_confidence: float = 0.0,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM insights WHERE user_id = ? AND confidence >= ?"
        params: list = [user_id, min_confidence]
        if insight_type:
            query += " AND insight_type = ?"
            params.append(insight_type)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY confidence DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def record_outcome(
        self,
        insight_id: str,
        success: bool,
        *,
        baseline_score: Optional[float] = None,
        actual_score: Optional[float] = None,
    ) -> bool:
        """Record validation/invalidation outcome. Updates confidence + utility."""
        now = _utcnow_iso()
        with self._tx() as conn:
            row = conn.execute(
                """SELECT confidence, validation_count, invalidation_count,
                          utility, apply_count, status
                   FROM insights WHERE insight_id = ?""",
                (insight_id,),
            ).fetchone()
            if not row:
                return False

            conf = row["confidence"]
            v_count = row["validation_count"]
            i_count = row["invalidation_count"]
            utility = row["utility"]
            apply_count = row["apply_count"] + 1

            if success:
                v_count += 1
                conf = min(1.0, conf + 0.05)
            else:
                i_count += 1
                conf = max(0.0, conf - 0.1)

            if baseline_score is not None and actual_score is not None:
                delta = actual_score - baseline_score
                utility = 0.3 * delta + 0.7 * utility

            conn.execute(
                """UPDATE insights
                   SET confidence = ?, validation_count = ?,
                       invalidation_count = ?, utility = ?,
                       apply_count = ?, last_validated = ?,
                       status = ?
                   WHERE insight_id = ?""",
                (
                    conf, v_count, i_count, utility, apply_count, now,
                    row["status"],  # preserve current status
                    insight_id,
                ),
            )
        return True

    def set_status(self, insight_id: str, status: str) -> bool:
        with self._tx() as conn:
            result = conn.execute(
                "UPDATE insights SET status = ? WHERE insight_id = ?",
                (status, insight_id),
            )
        return result.rowcount > 0

    def get_by_invalidation_status(
        self, status: str, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM insights WHERE status = ? LIMIT ?",
                (status, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "insight_id": row["insight_id"],
            "user_id": row["user_id"],
            "content": row["content"],
            "insight_type": row["insight_type"],
            "source_task_types": _parse_json(row["source_task_types_json"], []),
            "confidence": row["confidence"],
            "validation_count": row["validation_count"],
            "invalidation_count": row["invalidation_count"],
            "utility": row["utility"],
            "apply_count": row["apply_count"],
            "derivation_version": row["derivation_version"],
            "lineage_fingerprint": row["lineage_fingerprint"],
            "created_at": row["created_at"],
            "last_validated": row["last_validated"],
            "tags": _parse_json(row["tags_json"], []),
            "status": row["status"],
        }


# =========================================================================
# HeuristicStore
# =========================================================================

class HeuristicStore(_DerivedStoreBase):
    """Transferable reasoning patterns (ERL, 3 abstraction levels)."""

    def add(
        self,
        user_id: str,
        content: str,
        *,
        abstraction_level: str = "specific",
        source_task_types: Optional[List[str]] = None,
        confidence: float = 0.5,
        tags: Optional[List[str]] = None,
        heuristic_id: Optional[str] = None,
    ) -> str:
        hid = heuristic_id or str(uuid.uuid4())
        now = _utcnow_iso()

        with self._tx() as conn:
            conn.execute(
                """INSERT INTO heuristics
                   (heuristic_id, user_id, content, abstraction_level,
                    source_task_types_json, confidence,
                    derivation_version, lineage_fingerprint,
                    created_at, tags_json)
                   VALUES (?, ?, ?, ?, ?, ?, 1, '', ?, ?)""",
                (
                    hid, user_id, content, abstraction_level,
                    json.dumps(source_task_types or []),
                    confidence, now, json.dumps(tags or []),
                ),
            )
        return hid

    def get(self, heuristic_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM heuristics WHERE heuristic_id = ?",
                (heuristic_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_user(
        self,
        user_id: str,
        *,
        abstraction_level: Optional[str] = None,
        min_confidence: float = 0.0,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM heuristics WHERE user_id = ? AND confidence >= ?"
        params: list = [user_id, min_confidence]
        if abstraction_level:
            query += " AND abstraction_level = ?"
            params.append(abstraction_level)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY confidence DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def record_outcome(
        self,
        heuristic_id: str,
        success: bool,
        *,
        baseline_score: Optional[float] = None,
        actual_score: Optional[float] = None,
    ) -> bool:
        now = _utcnow_iso()
        with self._tx() as conn:
            row = conn.execute(
                """SELECT confidence, validation_count, invalidation_count,
                          utility, last_delta, apply_count, status
                   FROM heuristics WHERE heuristic_id = ?""",
                (heuristic_id,),
            ).fetchone()
            if not row:
                return False

            conf = row["confidence"]
            v_count = row["validation_count"]
            i_count = row["invalidation_count"]
            utility = row["utility"]
            apply_count = row["apply_count"] + 1
            delta = 0.0

            if success:
                v_count += 1
                conf = min(1.0, conf + 0.05)
            else:
                i_count += 1
                conf = max(0.0, conf - 0.1)

            if baseline_score is not None and actual_score is not None:
                delta = actual_score - baseline_score
                utility = 0.3 * delta + 0.7 * utility

            conn.execute(
                """UPDATE heuristics
                   SET confidence = ?, validation_count = ?,
                       invalidation_count = ?, utility = ?,
                       last_delta = ?, apply_count = ?, status = ?
                   WHERE heuristic_id = ?""",
                (
                    conf, v_count, i_count, utility,
                    delta, apply_count, row["status"],
                    heuristic_id,
                ),
            )
        return True

    def set_status(self, heuristic_id: str, status: str) -> bool:
        with self._tx() as conn:
            result = conn.execute(
                "UPDATE heuristics SET status = ? WHERE heuristic_id = ?",
                (status, heuristic_id),
            )
        return result.rowcount > 0

    def get_by_invalidation_status(
        self, status: str, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM heuristics WHERE status = ? LIMIT ?",
                (status, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "heuristic_id": row["heuristic_id"],
            "user_id": row["user_id"],
            "content": row["content"],
            "abstraction_level": row["abstraction_level"],
            "source_task_types": _parse_json(row["source_task_types_json"], []),
            "confidence": row["confidence"],
            "validation_count": row["validation_count"],
            "invalidation_count": row["invalidation_count"],
            "utility": row["utility"],
            "last_delta": row["last_delta"],
            "apply_count": row["apply_count"],
            "derivation_version": row["derivation_version"],
            "lineage_fingerprint": row["lineage_fingerprint"],
            "created_at": row["created_at"],
            "tags": _parse_json(row["tags_json"], []),
            "status": row["status"],
        }


# =========================================================================
# DerivedLineageStore
# =========================================================================

class DerivedLineageStore(_DerivedStoreBase):
    """Links derived objects to source raw events for traceability.

    Supports the three-tier invalidation model:
    - Given a source event, find all derived objects that depend on it
    - Given a derived object, find all source events it was built from
    - Contribution weight enables partial invalidation decisions
    """

    def add(
        self,
        derived_type: str,
        derived_id: str,
        source_event_id: str,
        *,
        contribution_weight: float = 1.0,
        lineage_id: Optional[str] = None,
    ) -> str:
        lid = lineage_id or str(uuid.uuid4())
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO derived_lineage
                   (lineage_id, derived_type, derived_id,
                    source_event_id, contribution_weight)
                   VALUES (?, ?, ?, ?, ?)""",
                (lid, derived_type, derived_id, source_event_id, contribution_weight),
            )
        return lid

    def add_batch(
        self,
        derived_type: str,
        derived_id: str,
        source_event_ids: List[str],
        *,
        weights: Optional[List[float]] = None,
    ) -> List[str]:
        """Add multiple lineage links at once."""
        w = weights or [1.0] * len(source_event_ids)
        ids = []
        with self._tx() as conn:
            for eid, weight in zip(source_event_ids, w):
                lid = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO derived_lineage
                       (lineage_id, derived_type, derived_id,
                        source_event_id, contribution_weight)
                       VALUES (?, ?, ?, ?, ?)""",
                    (lid, derived_type, derived_id, eid, weight),
                )
                ids.append(lid)
        return ids

    def get_sources(
        self, derived_type: str, derived_id: str
    ) -> List[Dict[str, Any]]:
        """Get all source events for a derived object."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT lineage_id, source_event_id, contribution_weight, created_at
                   FROM derived_lineage
                   WHERE derived_type = ? AND derived_id = ?""",
                (derived_type, derived_id),
            ).fetchall()
        return [
            {
                "lineage_id": r["lineage_id"],
                "source_event_id": r["source_event_id"],
                "contribution_weight": r["contribution_weight"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_dependents(
        self, source_event_id: str
    ) -> List[Dict[str, Any]]:
        """Get all derived objects that depend on a source event.

        This is the key query for invalidation cascades.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT lineage_id, derived_type, derived_id,
                          contribution_weight, created_at
                   FROM derived_lineage
                   WHERE source_event_id = ?""",
                (source_event_id,),
            ).fetchall()
        return [
            {
                "lineage_id": r["lineage_id"],
                "derived_type": r["derived_type"],
                "derived_id": r["derived_id"],
                "contribution_weight": r["contribution_weight"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_source_count(self, derived_type: str, derived_id: str) -> int:
        """Count source events for a derived object."""
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) FROM derived_lineage
                   WHERE derived_type = ? AND derived_id = ?""",
                (derived_type, derived_id),
            ).fetchone()
        return row[0] if row else 0

    def get_contribution_weight(
        self, derived_type: str, derived_id: str, source_event_id: str
    ) -> Optional[float]:
        """Get the contribution weight of a specific source to a derived object.

        Used by partial invalidation to decide severity.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT contribution_weight FROM derived_lineage
                   WHERE derived_type = ? AND derived_id = ? AND source_event_id = ?""",
                (derived_type, derived_id, source_event_id),
            ).fetchone()
        return row["contribution_weight"] if row else None

    def delete_for_derived(self, derived_type: str, derived_id: str) -> int:
        """Remove all lineage links for a derived object (e.g., before re-deriving)."""
        with self._tx() as conn:
            result = conn.execute(
                "DELETE FROM derived_lineage WHERE derived_type = ? AND derived_id = ?",
                (derived_type, derived_id),
            )
        return result.rowcount


# =========================================================================
# CognitionStore — Coordinator that holds all sub-stores
# =========================================================================

class CognitionStore:
    """Unified access to all v3 stores sharing a single SQLite connection.

    Usage:
        store = CognitionStore()  # or CognitionStore(db_path="...")
        store.events.add(content="...", user_id="...")
        store.beliefs.add(user_id="...", claim="...")
        store.lineage.add("belief", bid, event_id)
    """

    def __init__(self, db_path: Optional[str] = None):
        from dhee.core.events import RawEventStore, _default_db_path

        self.db_path = db_path or _default_db_path()

        # RawEventStore owns the connection and schema initialization
        self.events = RawEventStore(self.db_path)

        # All derived stores share the same connection + lock
        conn = self.events._conn
        lock = self.events._lock

        self.beliefs = BeliefStore(conn, lock)
        self.policies = PolicyStore(conn, lock)
        self.anchors = AnchorStore(conn, lock)
        self.insights = InsightStore(conn, lock)
        self.heuristics = HeuristicStore(conn, lock)
        self.lineage = DerivedLineageStore(conn, lock)

    def close(self) -> None:
        self.events.close()
