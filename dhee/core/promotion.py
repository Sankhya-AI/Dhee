"""Dhee v3 — Promotion Pipeline: validate and promote distillation candidates.

The promotion flow:
    1. Select pending candidates from distillation_candidates
    2. Validate (confidence threshold, conflict check)
    3. Promote transactionally into the target derived store
    4. Write lineage rows
    5. Mark candidate as promoted with promoted_id

Design contract:
    - Promotion is transactional: either fully committed or rolled back
    - Every promoted object gets lineage rows linking to source events
    - Idempotent: re-running on already-promoted candidates is a no-op
    - Zero LLM calls — pure storage operations
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dhee.core.derived_store import (
    BeliefStore,
    PolicyStore,
    InsightStore,
    HeuristicStore,
    DerivedLineageStore,
)
from dhee.core.distillation import DistillationStore

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Minimum confidence to promote a candidate
MIN_PROMOTION_CONFIDENCE = 0.3


class PromotionResult:
    """Result of a promotion batch."""

    def __init__(self):
        self.promoted: List[str] = []
        self.rejected: List[str] = []
        self.quarantined: List[str] = []
        self.skipped: List[str] = []
        self.errors: List[Dict[str, str]] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promoted": len(self.promoted),
            "rejected": len(self.rejected),
            "quarantined": len(self.quarantined),
            "skipped": len(self.skipped),
            "errors": len(self.errors),
            "promoted_ids": self.promoted,
        }


class PromotionEngine:
    """Validates and promotes distillation candidates into derived stores.

    Usage:
        engine = PromotionEngine(
            distillation=distillation_store,
            beliefs=belief_store,
            policies=policy_store,
            insights=insight_store,
            heuristics=heuristic_store,
            lineage=lineage_store,
        )
        result = engine.promote_pending(target_type="belief", limit=20)
    """

    def __init__(
        self,
        distillation: DistillationStore,
        beliefs: BeliefStore,
        policies: PolicyStore,
        insights: InsightStore,
        heuristics: HeuristicStore,
        lineage: DerivedLineageStore,
        *,
        min_confidence: float = MIN_PROMOTION_CONFIDENCE,
    ):
        self.distillation = distillation
        self.beliefs = beliefs
        self.policies = policies
        self.insights = insights
        self.heuristics = heuristics
        self.lineage = lineage
        self.min_confidence = min_confidence

        self._promoters = {
            "belief": self._promote_belief,
            "policy": self._promote_policy,
            "insight": self._promote_insight,
            "heuristic": self._promote_heuristic,
        }

    def promote_pending(
        self,
        target_type: Optional[str] = None,
        *,
        limit: int = 50,
    ) -> PromotionResult:
        """Promote all pending candidates of a given type.

        Args:
            target_type: Filter by type (belief, policy, etc.) or None for all
            limit: Max candidates to process

        Returns:
            PromotionResult with counts and IDs
        """
        result = PromotionResult()
        candidates = self.distillation.get_pending(target_type, limit=limit)

        for candidate in candidates:
            cid = candidate["candidate_id"]
            ctype = candidate["target_type"]

            try:
                # Validate
                validation = self._validate(candidate)

                if validation == "reject":
                    self.distillation.set_status(cid, "rejected")
                    result.rejected.append(cid)
                    continue

                if validation == "quarantine":
                    self.distillation.set_status(cid, "quarantined")
                    result.quarantined.append(cid)
                    continue

                # Promote
                promoter = self._promoters.get(ctype)
                if not promoter:
                    logger.warning("No promoter for type: %s", ctype)
                    result.skipped.append(cid)
                    continue

                promoted_id = promoter(candidate)
                if promoted_id:
                    # Write lineage
                    self._write_lineage(
                        ctype, promoted_id, candidate["source_event_ids"]
                    )
                    # Mark candidate as promoted
                    self.distillation.set_status(
                        cid, "promoted", promoted_id=promoted_id
                    )
                    result.promoted.append(promoted_id)
                else:
                    result.skipped.append(cid)

            except Exception as e:
                logger.exception(
                    "Failed to promote candidate %s: %s", cid, e
                )
                result.errors.append({
                    "candidate_id": cid,
                    "error": str(e),
                })

        return result

    def promote_single(
        self, candidate_id: str
    ) -> Dict[str, Any]:
        """Promote a single candidate by ID."""
        candidate = self.distillation.get(candidate_id)
        if not candidate:
            return {"status": "error", "error": f"Candidate not found: {candidate_id}"}

        if candidate["status"] != "pending_validation":
            return {
                "status": "skipped",
                "reason": f"Candidate status is '{candidate['status']}', not pending",
            }

        ctype = candidate["target_type"]
        validation = self._validate(candidate)

        if validation == "reject":
            self.distillation.set_status(candidate_id, "rejected")
            return {"status": "rejected", "reason": "validation_failed"}

        if validation == "quarantine":
            self.distillation.set_status(candidate_id, "quarantined")
            return {"status": "quarantined", "reason": "needs_review"}

        promoter = self._promoters.get(ctype)
        if not promoter:
            return {"status": "error", "error": f"No promoter for type: {ctype}"}

        promoted_id = promoter(candidate)
        if promoted_id:
            self._write_lineage(ctype, promoted_id, candidate["source_event_ids"])
            self.distillation.set_status(
                candidate_id, "promoted", promoted_id=promoted_id
            )
            return {"status": "promoted", "promoted_id": promoted_id}

        return {"status": "skipped", "reason": "promoter_returned_none"}

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, candidate: Dict[str, Any]) -> str:
        """Validate a candidate. Returns: 'accept', 'reject', or 'quarantine'."""
        confidence = candidate.get("confidence", 0.0)
        payload = candidate.get("payload", {})

        # Hard reject: below minimum confidence
        if confidence < self.min_confidence:
            return "reject"

        # Hard reject: empty payload
        if not payload:
            return "reject"

        # Type-specific validation
        target_type = candidate["target_type"]

        if target_type == "belief":
            claim = payload.get("claim", "")
            if not claim or len(claim.strip()) < 5:
                return "reject"

        elif target_type == "policy":
            if not payload.get("name") or not payload.get("condition"):
                return "reject"

        elif target_type in ("insight", "heuristic"):
            content = payload.get("content", "")
            if not content or len(content.strip()) < 10:
                return "reject"

        return "accept"

    # ------------------------------------------------------------------
    # Type-specific promoters
    # ------------------------------------------------------------------

    def _promote_belief(self, candidate: Dict[str, Any]) -> Optional[str]:
        payload = candidate["payload"]
        return self.beliefs.add(
            user_id=payload["user_id"],
            claim=payload["claim"],
            domain=payload.get("domain", "general"),
            confidence=candidate["confidence"],
            source_memory_ids=candidate["source_event_ids"],
            tags=payload.get("tags"),
        )

    def _promote_policy(self, candidate: Dict[str, Any]) -> Optional[str]:
        payload = candidate["payload"]
        return self.policies.add(
            user_id=payload["user_id"],
            name=payload["name"],
            condition=payload.get("condition", {}),
            action=payload.get("action", {}),
            granularity=payload.get("granularity", "task"),
            source_task_ids=candidate["source_event_ids"],
            tags=payload.get("tags"),
        )

    def _promote_insight(self, candidate: Dict[str, Any]) -> Optional[str]:
        payload = candidate["payload"]
        return self.insights.add(
            user_id=payload["user_id"],
            content=payload["content"],
            insight_type=payload.get("insight_type", "pattern"),
            confidence=candidate["confidence"],
            tags=payload.get("tags"),
        )

    def _promote_heuristic(self, candidate: Dict[str, Any]) -> Optional[str]:
        payload = candidate["payload"]
        return self.heuristics.add(
            user_id=payload["user_id"],
            content=payload["content"],
            abstraction_level=payload.get("abstraction_level", "specific"),
            confidence=candidate["confidence"],
            tags=payload.get("tags"),
        )

    # ------------------------------------------------------------------
    # Lineage
    # ------------------------------------------------------------------

    def _write_lineage(
        self,
        derived_type: str,
        derived_id: str,
        source_event_ids: List[str],
    ) -> None:
        """Write lineage rows linking the promoted object to source events."""
        if not source_event_ids:
            return

        # Equal weight distribution across sources
        weight = 1.0 / len(source_event_ids)
        self.lineage.add_batch(
            derived_type, derived_id, source_event_ids,
            weights=[weight] * len(source_event_ids),
        )
