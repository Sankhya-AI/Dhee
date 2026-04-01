"""Dhee v3 — Anchor Resolver: per-field candidates + confidence-weighted resolution.

Makes context extraction fallible, revisable, and auditable.

Instead of a single ContextAnchor with one confidence score, each field
(era, place, time_absolute, activity, etc.) gets competing candidates.
Resolution picks the best candidate per field. Re-anchoring is safe
because raw events are never touched.

Design contract:
    - Extraction produces candidates, not final truth
    - Same memory can hold alternate candidate anchors
    - Anchor correction does not mutate raw event history
    - Resolution is deterministic: highest confidence per field wins
    - Zero LLM calls — rule-based extraction + confidence scoring
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Fields that can have competing candidates
ANCHOR_FIELDS = frozenset({
    "era", "place", "place_type", "place_detail",
    "time_absolute", "time_range_start", "time_range_end",
    "time_derivation", "activity",
})


@dataclass
class AnchorCandidate:
    """A proposed value for a single anchor field."""

    candidate_id: str
    anchor_id: str
    field_name: str
    field_value: str
    confidence: float = 0.5
    extractor_source: str = "default"
    source_event_ids: List[str] = field(default_factory=list)
    derivation_version: int = 1
    status: str = "pending"  # pending | accepted | rejected | superseded


class AnchorCandidateStore:
    """Manages per-field anchor candidates in the database."""

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

    def submit(
        self,
        anchor_id: str,
        field_name: str,
        field_value: str,
        *,
        confidence: float = 0.5,
        extractor_source: str = "default",
        source_event_ids: Optional[List[str]] = None,
    ) -> str:
        """Submit a candidate for an anchor field. Returns candidate_id."""
        if field_name not in ANCHOR_FIELDS:
            raise ValueError(f"Invalid anchor field: {field_name}")

        cid = str(uuid.uuid4())
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO anchor_candidates
                   (candidate_id, anchor_id, field_name, field_value,
                    confidence, extractor_source, source_event_ids,
                    derivation_version, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'pending', ?)""",
                (
                    cid, anchor_id, field_name, field_value,
                    confidence, extractor_source,
                    json.dumps(source_event_ids or []),
                    _utcnow_iso(),
                ),
            )
        return cid

    def get_candidates(
        self,
        anchor_id: str,
        field_name: Optional[str] = None,
        *,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get candidates for an anchor, optionally filtered by field and status."""
        query = "SELECT * FROM anchor_candidates WHERE anchor_id = ?"
        params: list = [anchor_id]
        if field_name:
            query += " AND field_name = ?"
            params.append(field_name)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY confidence DESC"

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def set_status(self, candidate_id: str, status: str) -> bool:
        with self._lock:
            try:
                result = self._conn.execute(
                    "UPDATE anchor_candidates SET status = ? WHERE candidate_id = ?",
                    (status, candidate_id),
                )
                self._conn.commit()
                return result.rowcount > 0
            except Exception:
                self._conn.rollback()
                raise

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        source_ids = row["source_event_ids"]
        if isinstance(source_ids, str):
            try:
                source_ids = json.loads(source_ids)
            except (json.JSONDecodeError, TypeError):
                source_ids = []
        return {
            "candidate_id": row["candidate_id"],
            "anchor_id": row["anchor_id"],
            "field_name": row["field_name"],
            "field_value": row["field_value"],
            "confidence": row["confidence"],
            "extractor_source": row["extractor_source"],
            "source_event_ids": source_ids,
            "derivation_version": row["derivation_version"],
            "status": row["status"],
            "created_at": row["created_at"],
        }


class AnchorResolver:
    """Resolves anchor fields from competing candidates.

    Resolution strategy:
    1. For each anchor field, find all pending/accepted candidates
    2. Pick the highest-confidence candidate per field
    3. Mark winner as 'accepted', losers as 'superseded'
    4. Update the anchor row with resolved values

    Re-resolution: when new candidates arrive (correction, new evidence),
    call resolve() again — it re-evaluates all candidates.
    """

    def __init__(
        self,
        candidate_store: AnchorCandidateStore,
        anchor_store: "AnchorStore",
    ):
        self.candidates = candidate_store
        self.anchors = anchor_store

    def resolve(self, anchor_id: str) -> Dict[str, Any]:
        """Resolve all fields for an anchor. Returns the resolved field values.

        Steps:
        1. Get all non-rejected candidates grouped by field
        2. For each field, pick highest confidence
        3. Mark winners as accepted, others as superseded
        4. Update anchor with resolved values
        """
        resolved: Dict[str, str] = {}
        resolution_details: Dict[str, Dict[str, Any]] = {}

        for field_name in ANCHOR_FIELDS:
            candidates = self.candidates.get_candidates(
                anchor_id, field_name
            )
            # Filter to only pending/accepted (not rejected)
            active = [
                c for c in candidates
                if c["status"] in ("pending", "accepted")
            ]

            if not active:
                continue

            # Sort by confidence descending, then by created_at ascending (earlier = better tiebreak)
            active.sort(key=lambda c: (-c["confidence"], c["created_at"]))
            winner = active[0]

            resolved[field_name] = winner["field_value"]
            resolution_details[field_name] = {
                "value": winner["field_value"],
                "confidence": winner["confidence"],
                "source": winner["extractor_source"],
                "candidate_id": winner["candidate_id"],
                "competing_count": len(active),
            }

            # Mark winner accepted, others superseded
            for c in active:
                if c["candidate_id"] == winner["candidate_id"]:
                    self.candidates.set_status(c["candidate_id"], "accepted")
                else:
                    self.candidates.set_status(c["candidate_id"], "superseded")

        # Update anchor with resolved values
        if resolved:
            self.anchors.update_fields(anchor_id, **resolved)

        return {
            "anchor_id": anchor_id,
            "resolved_fields": resolved,
            "details": resolution_details,
        }

    def re_anchor(
        self,
        anchor_id: str,
        field_name: str,
        new_value: str,
        *,
        confidence: float = 0.9,
        source: str = "user_correction",
        source_event_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Submit a correction for a specific field and re-resolve.

        The user says "no, the place was Bengaluru, not Ghazipur."
        This submits a high-confidence candidate and re-runs resolution.
        """
        # Submit the correction as a new candidate
        cid = self.candidates.submit(
            anchor_id=anchor_id,
            field_name=field_name,
            field_value=new_value,
            confidence=confidence,
            extractor_source=source,
            source_event_ids=source_event_ids,
        )

        # Re-resolve just this field (and all others for consistency)
        result = self.resolve(anchor_id)
        result["correction_candidate_id"] = cid
        return result

    def extract_and_submit(
        self,
        anchor_id: str,
        content: str,
        *,
        source_event_ids: Optional[List[str]] = None,
    ) -> List[str]:
        """Rule-based extraction of anchor candidates from content.

        Extracts candidates for each field it can identify. Returns
        list of candidate_ids.

        Zero LLM calls — keyword/pattern matching only.
        """
        candidates_created: List[str] = []
        eids = source_event_ids or []
        lower = content.lower()

        # Activity detection
        activity_keywords = {
            "coding": ["coding", "programming", "debug", "commit", "deploy", "refactor"],
            "meeting": ["meeting", "standup", "call", "sync", "discussion"],
            "research": ["research", "reading", "paper", "study", "learn"],
            "travel": ["travel", "flight", "airport", "train", "driving"],
            "writing": ["writing", "blog", "document", "email", "report"],
        }
        for activity, keywords in activity_keywords.items():
            matches = sum(1 for kw in keywords if kw in lower)
            if matches >= 1:
                confidence = min(0.3 + 0.15 * matches, 0.85)
                cid = self.candidates.submit(
                    anchor_id=anchor_id,
                    field_name="activity",
                    field_value=activity,
                    confidence=confidence,
                    extractor_source="keyword_activity",
                    source_event_ids=eids,
                )
                candidates_created.append(cid)

        # Place type detection
        place_types = {
            "office": ["office", "workplace", "desk", "cubicle"],
            "home": ["home", "house", "apartment", "flat"],
            "school": ["school", "university", "college", "campus", "class"],
            "travel": ["airport", "station", "hotel", "flight"],
        }
        for ptype, keywords in place_types.items():
            if any(kw in lower for kw in keywords):
                cid = self.candidates.submit(
                    anchor_id=anchor_id,
                    field_name="place_type",
                    field_value=ptype,
                    confidence=0.6,
                    extractor_source="keyword_place_type",
                    source_event_ids=eids,
                )
                candidates_created.append(cid)

        return candidates_created
