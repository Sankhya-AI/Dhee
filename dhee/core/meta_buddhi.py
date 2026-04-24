"""MetaBuddhi — proposal machinery for self-referential strategy updates.

Native to Dhee (no opt-in flag). Today the module:

  - Proposes candidate strategy deltas with lineage (``ImprovementAttempt``
    records are persisted as versioned JSON, diffable, rollback-safe).
  - Scores candidates against EMA-smoothed samskara signals when available.
  - Skips promotion unless ``_MIN_EVAL_COUNT`` and ``_PROMOTION_THRESHOLD``
    are met (fail-safe default).

What closes the loop in the next release (Movement 4 of the public plan):

  - Replay-based assessment of each candidate against recorded sessions
    in ``~/.claude/projects/…``.
  - Automatic commit/rollback driven by the replay outcome.
  - Group-relative confidence updates (Dr.RTL-style) when multiple
    candidates fire on the same task type.

Meta DGM-Hyperagents framing (arXiv:2603.19461):
  1. MetaBuddhi proposes a strategy change (e.g., increase keyword_weight)
  2. The candidate is assessed against recorded sessions
  3. Samskara-derived signals measure whether retrieval quality improved
  4. If improved → promote with lineage. If degraded → rollback.
  5. The RULES for proposing changes are themselves updated by outcomes.
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
# M4.2b: per-task-type "don't let one group tank" guardrail. If any single
# task-type group regresses by this much vs. the parent baseline, the
# candidate is rolled back even if the aggregated delta looks positive.
_GROUP_CATASTROPHE_THRESHOLD = 0.06
# M4.2b: minimum samples per group before we trust its delta. Below this,
# the group contributes only to the rolling baseline, not to the decision.
_MIN_GROUP_SAMPLES = 2
# M4.3: newly promoted strategies stay under watch for a short online window.
# If they regress beyond this threshold, rollback is automatic.
_POST_PROMOTION_MIN_EVAL_COUNT = 5
_POST_PROMOTION_REGRESSION_THRESHOLD = 0.04
_POST_PROMOTION_GROUP_CATASTROPHE_THRESHOLD = 0.06


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
    # M4.2b: per-sample task_type tags (one dict per eval call).
    # Kept alongside eval_scores so legacy untagged attempts still load.
    eval_samples: List[Dict[str, Any]] = field(default_factory=list)
    # M4.2b: snapshot of the parent strategy's per-task-type baseline at
    # propose time, so the resolution delta is computed against what the
    # parent actually scored on the SAME task types the candidate saw.
    parent_baseline_by_task: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # M4.2b: populated at resolve time — per-task-type candidate delta
    # vs. parent baseline; surfaced via get_stats() for debuggability.
    group_deltas: Dict[str, float] = field(default_factory=dict)
    # M4.3: parent global baseline at propose-time. Used for post-promotion
    # regression checks so we compare against a stable reference.
    parent_global_baseline: float = 0.5
    # M4.3: promoted strategies are watched for the next N evaluations.
    post_promotion_status: str = "not_started"  # not_started|watching|validated|rolled_back
    post_promotion_scores: List[float] = field(default_factory=list)
    post_promotion_samples: List[Dict[str, Any]] = field(default_factory=list)
    post_promotion_group_deltas: Dict[str, float] = field(default_factory=dict)
    post_promotion_delta: Optional[float] = None
    post_promotion_resolved_at: Optional[float] = None

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
            "eval_samples": self.eval_samples[-20:],
            "parent_baseline_by_task": dict(self.parent_baseline_by_task),
            "group_deltas": dict(self.group_deltas),
            "parent_global_baseline": float(self.parent_global_baseline),
            "post_promotion_status": self.post_promotion_status,
            "post_promotion_scores": self.post_promotion_scores[-50:],
            "post_promotion_samples": self.post_promotion_samples[-50:],
            "post_promotion_group_deltas": dict(self.post_promotion_group_deltas),
            "post_promotion_delta": self.post_promotion_delta,
            "post_promotion_resolved_at": self.post_promotion_resolved_at,
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
        self._watching_attempt: Optional[str] = None
        # M4.2b: per-task-type rolling baseline. Updated on every
        # record_evaluation call — even when no attempt is pending — so
        # the next proposal has a fresh parent baseline to snapshot.
        # Shape: {task_type: {"mean": float, "n": int, "last_updated": float}}
        self._group_stats: Dict[str, Dict[str, Any]] = {}
        self._load_attempts()
        self._load_group_stats()

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
        # M4.3: no overlapping mutations while a newly promoted strategy is
        # still under post-promotion validation.
        if self._watching_attempt:
            watching = self._attempts.get(self._watching_attempt)
            if (
                watching
                and watching.status == "promoted"
                and watching.post_promotion_status == "watching"
            ):
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
            parent_global_baseline=active.avg_score if active.eval_scores else 0.5,
        )
        # M4.2b: snapshot parent's per-task-type baseline right now, so
        # resolution compares apples-to-apples per task type.
        attempt.parent_baseline_by_task = {
            t: {"mean": float(stat["mean"]), "n": int(stat["n"])}
            for t, stat in self._group_stats.items()
            if int(stat.get("n", 0)) >= _MIN_GROUP_SAMPLES
        }

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

    def record_evaluation(
        self,
        score: float,
        *,
        task_type: Optional[str] = None,
        source: Optional[str] = None,
        signal_components: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Record an evaluation score for the pending improvement.

        Call this after each interaction while a candidate is being
        evaluated. The optional ``task_type`` tag enables group-relative
        resolution (M4.2b): the candidate's per-group delta is compared
        against the parent's per-group baseline, so a +0.03 lift on an
        easy task type no longer drowns out a −0.10 regression on a
        hard one.

        Returns the resolution status if the attempt has been resolved,
        or None if still evaluating.
        """
        # Always update the rolling per-task-type baseline, even when no
        # attempt is pending. This is what the NEXT proposal will
        # snapshot as the parent baseline.
        if task_type:
            self._update_group_stat(task_type, score)

        if not self._pending_attempt:
            # M4.3: no candidate pending; use incoming scores to validate the
            # most recently promoted strategy during its watch window.
            return self._record_post_promotion_signal(
                score=score,
                task_type=task_type,
                source=source,
                signal_components=signal_components,
            )

        attempt = self._attempts.get(self._pending_attempt)
        if not attempt or attempt.status != "evaluating":
            return None

        attempt.eval_scores.append(score)
        attempt.eval_samples.append({
            "score": float(score),
            "task_type": task_type,
            "source": source or "unknown",
            "signal_components": dict(signal_components or {}),
            "ts": time.time(),
        })

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

    def _update_group_stat(self, task_type: str, score: float) -> None:
        """Incremental mean update for the per-task-type baseline.

        Welford-style running mean to avoid ever-growing score buffers.
        """
        stat = self._group_stats.get(task_type)
        if stat is None:
            self._group_stats[task_type] = {
                "mean": float(score),
                "n": 1,
                "last_updated": time.time(),
            }
            self._save_group_stats()
            return
        n = int(stat.get("n", 0)) + 1
        mean = float(stat.get("mean", 0.0))
        mean += (float(score) - mean) / n
        stat["mean"] = mean
        stat["n"] = n
        stat["last_updated"] = time.time()
        self._save_group_stats()

    def _resolve_attempt(self, attempt: ImprovementAttempt) -> str:
        """Judge whether the improvement helped.

        Resolution is group-relative when the candidate's eval_samples
        carry task_type tags (M4.2b). A candidate is promoted iff:

          * the aggregated per-group delta (weighted by min(n_cand, n_parent)
            per group) meets ``_PROMOTION_THRESHOLD``, AND
          * no single group regresses by more than
            ``_GROUP_CATASTROPHE_THRESHOLD`` — an easy group cannot drown
            out a hard-group regression.

        If no tagged samples exist, we fall back to the original
        global delta so existing call sites keep working.
        """
        delta, group_deltas, basis = self._compute_delta(attempt)
        attempt.group_deltas = group_deltas

        catastrophic_group = None
        for t, d in group_deltas.items():
            if d <= -_GROUP_CATASTROPHE_THRESHOLD:
                catastrophic_group = (t, d)
                break

        promote = (
            delta >= _PROMOTION_THRESHOLD
            and catastrophic_group is None
        )

        if promote:
            self._store.promote(attempt.strategy_id)
            attempt.status = "promoted"
            attempt.post_promotion_status = "watching"
            self._watching_attempt = attempt.id
            logger.info(
                "MetaBuddhi promoted strategy: %s (delta=+%.3f basis=%s groups=%s)",
                attempt.dimension, delta, basis, group_deltas,
            )
        else:
            self._store.rollback(attempt.strategy_id)
            attempt.status = "rolled_back"
            attempt.post_promotion_status = "rolled_back"
            attempt.post_promotion_resolved_at = time.time()
            if self._watching_attempt == attempt.id:
                self._watching_attempt = None
            if catastrophic_group is not None:
                logger.info(
                    "MetaBuddhi rolled back: %s (delta=%.3f basis=%s) — "
                    "catastrophic group regression: %s=%.3f",
                    attempt.dimension, delta, basis,
                    catastrophic_group[0], catastrophic_group[1],
                )
            else:
                logger.info(
                    "MetaBuddhi rolled back: %s (delta=%.3f basis=%s groups=%s)",
                    attempt.dimension, delta, basis, group_deltas,
                )

        attempt.resolved_at = time.time()
        self._pending_attempt = None
        self._save_attempts()
        return attempt.status

    def _compute_delta(
        self, attempt: ImprovementAttempt
    ) -> Tuple[float, Dict[str, float], str]:
        """Return (aggregated_delta, per_group_deltas, basis).

        ``basis`` is ``"group_relative"`` when the candidate's eval_samples
        are tagged with task_type AND at least one group matches the
        parent's baseline snapshot. Otherwise it falls back to
        ``"global"`` (original behavior).
        """
        candidate_by_group: Dict[str, List[float]] = {}
        for sample in attempt.eval_samples:
            t = sample.get("task_type")
            if not t:
                continue
            candidate_by_group.setdefault(t, []).append(float(sample["score"]))

        parent_baseline = attempt.parent_baseline_by_task or {}

        shared_groups = [
            t for t, scores in candidate_by_group.items()
            if len(scores) >= _MIN_GROUP_SAMPLES and t in parent_baseline
        ]

        if shared_groups:
            group_deltas: Dict[str, float] = {}
            weighted_sum = 0.0
            total_weight = 0.0
            for t in shared_groups:
                cand_scores = candidate_by_group[t]
                cand_mean = sum(cand_scores) / len(cand_scores)
                parent_mean = float(parent_baseline[t]["mean"])
                parent_n = int(parent_baseline[t]["n"])
                weight = float(min(len(cand_scores), parent_n))
                d = cand_mean - parent_mean
                group_deltas[t] = d
                weighted_sum += d * weight
                total_weight += weight
            aggregated = weighted_sum / total_weight if total_weight > 0 else 0.0
            return aggregated, group_deltas, "group_relative"

        # Fallback: global delta (original behavior)
        parent = self._store.get(attempt.parent_strategy_id)
        parent_avg = parent.avg_score if parent and parent.eval_scores else 0.5
        candidate_avg = (
            sum(attempt.eval_scores) / len(attempt.eval_scores)
            if attempt.eval_scores else 0.0
        )
        return candidate_avg - parent_avg, {}, "global"

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
            "watching_attempt": (
                self._attempts[self._watching_attempt].to_dict()
                if self._watching_attempt and self._watching_attempt in self._attempts
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
            # M4.2b surface — per-task-type rolling baselines.
            "group_baselines": {
                t: {"mean": float(s.get("mean", 0.0)), "n": int(s.get("n", 0))}
                for t, s in self._group_stats.items()
            },
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
                f.write(json.dumps({"_watching": self._watching_attempt}) + "\n")
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
                        if "_watching" in data:
                            self._watching_attempt = data["_watching"]
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
                            eval_samples=data.get("eval_samples", []),
                            parent_baseline_by_task=data.get(
                                "parent_baseline_by_task", {}
                            ),
                            group_deltas=data.get("group_deltas", {}),
                            parent_global_baseline=float(
                                data.get("parent_global_baseline", 0.5)
                            ),
                            post_promotion_status=data.get(
                                "post_promotion_status", "not_started"
                            ),
                            post_promotion_scores=data.get(
                                "post_promotion_scores", []
                            ),
                            post_promotion_samples=data.get(
                                "post_promotion_samples", []
                            ),
                            post_promotion_group_deltas=data.get(
                                "post_promotion_group_deltas", {}
                            ),
                            post_promotion_delta=data.get("post_promotion_delta"),
                            post_promotion_resolved_at=data.get(
                                "post_promotion_resolved_at"
                            ),
                        )
                        self._attempts[attempt.id] = attempt
                    except (KeyError, TypeError):
                        continue
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Failed to load attempts: %s", e)

    def _record_post_promotion_signal(
        self,
        *,
        score: float,
        task_type: Optional[str],
        source: Optional[str],
        signal_components: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        attempt = (
            self._attempts.get(self._watching_attempt) if self._watching_attempt else None
        )
        if (
            not attempt
            or attempt.status != "promoted"
            or attempt.post_promotion_status not in {"watching", "not_started"}
        ):
            return None

        attempt.post_promotion_status = "watching"
        attempt.post_promotion_scores.append(float(score))
        attempt.post_promotion_samples.append(
            {
                "score": float(score),
                "task_type": task_type,
                "source": source or "unknown",
                "signal_components": dict(signal_components or {}),
                "ts": time.time(),
            }
        )
        if len(attempt.post_promotion_scores) < _POST_PROMOTION_MIN_EVAL_COUNT:
            self._save_attempts()
            return "watching"

        post_avg = sum(attempt.post_promotion_scores) / len(
            attempt.post_promotion_scores
        )
        global_delta = post_avg - float(attempt.parent_global_baseline)
        attempt.post_promotion_delta = global_delta

        # Group-level post-promotion regression checks.
        sample_groups: Dict[str, List[float]] = {}
        for sample in attempt.post_promotion_samples:
            t = sample.get("task_type")
            if not t:
                continue
            sample_groups.setdefault(str(t), []).append(float(sample["score"]))

        post_group_deltas: Dict[str, float] = {}
        for task_name, scores in sample_groups.items():
            if len(scores) < _MIN_GROUP_SAMPLES:
                continue
            parent = attempt.parent_baseline_by_task.get(task_name)
            if not parent:
                continue
            parent_mean = float(parent.get("mean", 0.5))
            post_group_deltas[task_name] = (sum(scores) / len(scores)) - parent_mean
        attempt.post_promotion_group_deltas = post_group_deltas

        catastrophic_group = None
        for task_name, delta in post_group_deltas.items():
            if delta <= -_POST_PROMOTION_GROUP_CATASTROPHE_THRESHOLD:
                catastrophic_group = (task_name, delta)
                break

        if (
            global_delta <= -_POST_PROMOTION_REGRESSION_THRESHOLD
            or catastrophic_group is not None
        ):
            self._store.rollback(attempt.strategy_id)
            attempt.status = "rolled_back"
            attempt.post_promotion_status = "rolled_back"
            attempt.post_promotion_resolved_at = time.time()
            self._watching_attempt = None
            if catastrophic_group is not None:
                logger.info(
                    "MetaBuddhi post-promotion rollback: %s due to group %s=%.3f",
                    attempt.dimension, catastrophic_group[0], catastrophic_group[1],
                )
            else:
                logger.info(
                    "MetaBuddhi post-promotion rollback: %s (delta=%.3f)",
                    attempt.dimension, global_delta,
                )
            self._save_attempts()
            return "rolled_back"

        attempt.post_promotion_status = "validated"
        attempt.post_promotion_resolved_at = time.time()
        self._watching_attempt = None
        self._save_attempts()
        logger.info(
            "MetaBuddhi post-promotion validated: %s (delta=+%.3f)",
            attempt.dimension, global_delta,
        )
        return "validated"

    # ------------------------------------------------------------------
    # M4.2b: group-baseline persistence
    # ------------------------------------------------------------------

    def _group_stats_path(self) -> str:
        return os.path.join(self._dir, "group_stats.json")

    def _save_group_stats(self) -> None:
        try:
            with open(self._group_stats_path(), "w", encoding="utf-8") as f:
                json.dump(self._group_stats, f, ensure_ascii=False)
        except OSError as e:
            logger.debug("Failed to save group stats: %s", e)

    def _load_group_stats(self) -> None:
        path = self._group_stats_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._group_stats = {
                    str(k): {
                        "mean": float(v.get("mean", 0.0)),
                        "n": int(v.get("n", 0)),
                        "last_updated": float(v.get("last_updated", 0.0)),
                    }
                    for k, v in data.items()
                    if isinstance(v, dict)
                }
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug("Failed to load group stats: %s", e)
