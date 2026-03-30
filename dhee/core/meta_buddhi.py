"""MetaBuddhi — the improvement procedure that improves itself.

Based on Meta's DGM-Hyperagents (arXiv:2603.19461): self-referential
meta-agents that modify their own improvement procedure.

The DGM-H insight: the agent doesn't just improve at tasks — it improves
at improving. The meta-level feedback loop:

  1. MetaBuddhi proposes a strategy change (e.g., increase keyword_weight)
  2. The system runs with the new strategy for N interactions
  3. Samskara signals measure whether retrieval/answer quality improved
  4. If improved → promote the strategy. If degraded → rollback.
  5. The RULES for proposing changes are themselves updated by outcomes.

This is the self-referential loop: MetaBuddhi modifies the weights
that Buddhi uses, and the results modify how MetaBuddhi proposes changes.

Strategies are stored as versioned JSON files — fully inspectable,
diffable, and rollback-safe.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from dhee.core.strategy import RetrievalStrategy, StrategyStore

logger = logging.getLogger(__name__)

# Tunable knobs and their valid ranges
_TUNABLE_FIELDS = {
    "semantic_weight": (0.3, 0.95),
    "keyword_weight": (0.05, 0.7),
    "recency_boost": (0.0, 0.2),
    "strength_floor": (0.0, 0.3),
    "contrastive_boost": (0.0, 0.4),
    "heuristic_relevance_weight": (0.0, 0.3),
    "insight_budget": (3, 20),
    "memory_budget": (5, 30),
}

# How many evaluations before judging a candidate
_MIN_EVAL_COUNT = 5
# Minimum improvement to justify promotion
_PROMOTION_THRESHOLD = 0.03


@dataclass
class ImprovementAttempt:
    """A single proposed change to the retrieval strategy."""

    id: str
    strategy_id: str                     # the candidate strategy
    parent_strategy_id: str              # the strategy it mutated from
    dimension: str                       # which field was changed
    old_value: float
    new_value: float
    rationale: str
    proposed_at: float
    status: str = "evaluating"           # evaluating | promoted | rolled_back | abandoned
    eval_scores: List[float] = field(default_factory=list)
    resolved_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "parent_strategy_id": self.parent_strategy_id,
            "dimension": self.dimension,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "rationale": self.rationale,
            "proposed_at": self.proposed_at,
            "status": self.status,
            "eval_scores": self.eval_scores[-20:],
            "resolved_at": self.resolved_at,
        }


class MetaBuddhi:
    """Self-referential cognition: the improvement procedure that improves itself.

    Operates on a simple loop:
      propose → evaluate → promote/rollback → learn from the decision

    The learning happens implicitly: the vasana signals from Samskara
    tell MetaBuddhi which dimensions are degrading, so it focuses
    proposals on those dimensions. Successful proposals reinforce
    the direction; failed ones reverse it.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        strategy_store: Optional[StrategyStore] = None,
    ):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "meta_buddhi"
        )
        os.makedirs(self._dir, exist_ok=True)

        self._store = strategy_store or StrategyStore(
            data_dir=os.path.join(self._dir, "strategies")
        )
        self._attempts: Dict[str, ImprovementAttempt] = {}
        self._pending_attempt: Optional[str] = None
        self._load_attempts()

    @property
    def strategy_store(self) -> StrategyStore:
        return self._store

    def get_active_strategy(self) -> RetrievalStrategy:
        return self._store.get_active()

    # ------------------------------------------------------------------
    # Propose
    # ------------------------------------------------------------------

    def propose_improvement(
        self,
        dimension: Optional[str] = None,
        vasana_report: Optional[Dict[str, Any]] = None,
    ) -> Optional[ImprovementAttempt]:
        """Propose a strategy mutation based on current signals.

        If dimension is given, mutate that field. Otherwise, auto-select
        the most degrading dimension from the vasana report.

        Returns None if there's already a pending evaluation.
        """
        # Only one active evaluation at a time
        if self._pending_attempt:
            pending = self._attempts.get(self._pending_attempt)
            if pending and pending.status == "evaluating":
                return None

        active = self._store.get_active()

        # Pick dimension to improve
        if not dimension:
            dimension = self._select_dimension(vasana_report)
        if not dimension or dimension not in _TUNABLE_FIELDS:
            return None

        # Compute mutation
        lo, hi = _TUNABLE_FIELDS[dimension]
        current_val = getattr(active, dimension)
        direction = self._mutation_direction(dimension, vasana_report)
        step = (hi - lo) * 0.1  # 10% of range
        new_val = current_val + direction * step

        # For integer fields
        if isinstance(current_val, int):
            new_val = int(round(new_val))
            lo, hi = int(lo), int(hi)

        new_val = max(lo, min(hi, new_val))

        # Don't propose no-ops
        if abs(new_val - current_val) < 1e-6:
            return None

        # Create candidate strategy
        candidate = RetrievalStrategy(
            id=str(uuid.uuid4()),
            version=active.version + 1,
            name=f"{active.name}_v{active.version + 1}",
            description=f"Mutated {dimension}: {current_val} → {new_val}",
            parent_id=active.id,
            status="candidate",
            **{
                k: getattr(active, k) for k in _TUNABLE_FIELDS
                if k != dimension
            },
            **{dimension: new_val},
        )
        self._store.save(candidate)

        # Create attempt
        rationale = self._build_rationale(dimension, current_val, new_val, vasana_report)
        attempt = ImprovementAttempt(
            id=str(uuid.uuid4()),
            strategy_id=candidate.id,
            parent_strategy_id=active.id,
            dimension=dimension,
            old_value=current_val,
            new_value=new_val,
            rationale=rationale,
            proposed_at=time.time(),
        )
        self._attempts[attempt.id] = attempt
        self._pending_attempt = attempt.id
        self._save_attempts()

        logger.info(
            "MetaBuddhi proposed: %s %s → %s (%s)",
            dimension, current_val, new_val, rationale,
        )
        return attempt

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------

    def record_evaluation(self, score: float) -> Optional[str]:
        """Record an evaluation score for the pending improvement.

        Call this after each interaction while a candidate is being evaluated.
        Returns the resolution status if the attempt has been resolved,
        or None if still evaluating.
        """
        if not self._pending_attempt:
            return None

        attempt = self._attempts.get(self._pending_attempt)
        if not attempt or attempt.status != "evaluating":
            return None

        attempt.eval_scores.append(score)

        # Also track on the candidate strategy
        candidate = self._store.get(attempt.strategy_id)
        if candidate:
            candidate.eval_scores.append(score)
            candidate.eval_count += 1
            self._store.save(candidate)

        # Enough data to judge?
        if len(attempt.eval_scores) >= _MIN_EVAL_COUNT:
            return self._resolve_attempt(attempt)

        self._save_attempts()
        return None

    def _resolve_attempt(self, attempt: ImprovementAttempt) -> str:
        """Judge whether the improvement helped."""
        parent = self._store.get(attempt.parent_strategy_id)
        parent_avg = parent.avg_score if parent and parent.eval_scores else 0.5
        candidate_avg = (
            sum(attempt.eval_scores) / len(attempt.eval_scores)
            if attempt.eval_scores else 0.0
        )

        delta = candidate_avg - parent_avg

        if delta >= _PROMOTION_THRESHOLD:
            # Improvement confirmed — promote
            self._store.promote(attempt.strategy_id)
            attempt.status = "promoted"
            logger.info(
                "MetaBuddhi promoted strategy: %s (delta=+%.3f)",
                attempt.dimension, delta,
            )
        else:
            # No improvement or regression — rollback
            self._store.rollback(attempt.strategy_id)
            attempt.status = "rolled_back"
            logger.info(
                "MetaBuddhi rolled back: %s (delta=%.3f)",
                attempt.dimension, delta,
            )

        attempt.resolved_at = time.time()
        self._pending_attempt = None
        self._save_attempts()
        return attempt.status

    # ------------------------------------------------------------------
    # Dimension selection (the meta-meta level)
    # ------------------------------------------------------------------

    def _select_dimension(
        self, vasana_report: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        """Pick the dimension most in need of improvement."""
        if not vasana_report:
            # Random exploration
            return random.choice(list(_TUNABLE_FIELDS.keys()))

        # Map vasana dimensions to strategy fields
        vasana_to_strategy = {
            "retrieval_precision": "semantic_weight",
            "retrieval_recall": "keyword_weight",
            "answer_quality": "insight_budget",
            "fact_extraction": "memory_budget",
            "dedup_quality": "strength_floor",
        }

        # Find the most degrading vasana
        worst_dim = None
        worst_strength = 0.0
        for name, report in vasana_report.items():
            strength = report.get("strength", 0.0) if isinstance(report, dict) else 0.0
            if strength < worst_strength:
                worst_strength = strength
                worst_dim = name

        if worst_dim and worst_dim in vasana_to_strategy:
            return vasana_to_strategy[worst_dim]

        return random.choice(list(_TUNABLE_FIELDS.keys()))

    def _mutation_direction(
        self,
        dimension: str,
        vasana_report: Optional[Dict[str, Any]],
    ) -> float:
        """Decide whether to increase (+1) or decrease (-1) a dimension.

        Uses past attempt outcomes to learn which direction works.
        """
        # Check history: which direction worked for this dimension?
        ups, downs = 0, 0
        for attempt in self._attempts.values():
            if attempt.dimension != dimension:
                continue
            if attempt.status == "promoted":
                if attempt.new_value > attempt.old_value:
                    ups += 1
                else:
                    downs += 1
            elif attempt.status == "rolled_back":
                if attempt.new_value > attempt.old_value:
                    downs += 1
                else:
                    ups += 1

        if ups > downs:
            return 1.0
        elif downs > ups:
            return -1.0
        # No history — random
        return random.choice([-1.0, 1.0])

    def _build_rationale(
        self,
        dimension: str,
        old_val: Any,
        new_val: Any,
        vasana_report: Optional[Dict[str, Any]],
    ) -> str:
        """Build a human-readable rationale for the proposed change."""
        direction = "increase" if new_val > old_val else "decrease"
        reason = "exploratory mutation"
        if vasana_report:
            degrading = [
                name for name, v in vasana_report.items()
                if isinstance(v, dict) and v.get("strength", 0) < -0.1
            ]
            if degrading:
                reason = f"degrading vasanas: {', '.join(degrading[:3])}"
        return f"{direction} {dimension} ({old_val} → {new_val}): {reason}"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        active = self._store.get_active()
        return {
            "active_strategy": active.to_dict() if active else None,
            "pending_attempt": (
                self._attempts[self._pending_attempt].to_dict()
                if self._pending_attempt and self._pending_attempt in self._attempts
                else None
            ),
            "total_attempts": len(self._attempts),
            "promoted": sum(
                1 for a in self._attempts.values() if a.status == "promoted"
            ),
            "rolled_back": sum(
                1 for a in self._attempts.values() if a.status == "rolled_back"
            ),
            "strategies_total": len(self._store.list_all()),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_attempts(self) -> None:
        path = os.path.join(self._dir, "attempts.jsonl")
        try:
            with open(path, "w", encoding="utf-8") as f:
                for a in self._attempts.values():
                    f.write(json.dumps(a.to_dict(), ensure_ascii=False) + "\n")
                # Also save pending pointer
                f.write(json.dumps({"_pending": self._pending_attempt}) + "\n")
        except OSError as e:
            logger.debug("Failed to save attempts: %s", e)

    def _load_attempts(self) -> None:
        path = os.path.join(self._dir, "attempts.jsonl")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if "_pending" in data:
                            self._pending_attempt = data["_pending"]
                            continue
                        attempt = ImprovementAttempt(
                            id=data["id"],
                            strategy_id=data["strategy_id"],
                            parent_strategy_id=data["parent_strategy_id"],
                            dimension=data["dimension"],
                            old_value=data["old_value"],
                            new_value=data["new_value"],
                            rationale=data.get("rationale", ""),
                            proposed_at=data.get("proposed_at", time.time()),
                            status=data.get("status", "evaluating"),
                            eval_scores=data.get("eval_scores", []),
                            resolved_at=data.get("resolved_at"),
                        )
                        self._attempts[attempt.id] = attempt
                    except (KeyError, TypeError):
                        continue
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Failed to load attempts: %s", e)
