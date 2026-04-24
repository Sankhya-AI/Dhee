"""Replay-based RL gate — the piece that flips ``model_improved`` True.

The gate answers exactly one question: does the candidate model beat the
incumbent on a held-out replay corpus by at least ``GATE_PROMOTE_DELTA``?
If yes, promotion is authorized; otherwise the candidate is retained on
disk but the runtime keeps serving the incumbent.

Design contract (matches ProgressiveTrainer's non-negotiables):

1. **No silent promotion.** A missing corpus, missing evaluator, or
   below-threshold delta all return a structured ``GateVerdict`` with
   ``passed=False`` and a specific ``reason``. Only an evidence-backed
   improvement flips ``passed=True``.
2. **Pluggable evaluator.** Callers can pass any
   ``Callable[[model_path, corpus], float]``. The default picks up the
   karma-based scorer when ``numpy`` + ``dhee.training.karma`` import;
   otherwise the gate honestly reports ``no_evaluator``.
3. **Corpus is a directory of JSONL shards.** Each line is a replay
   record ``{"prompt": ..., "expected": ..., "metadata": {...}}``. We
   only need ``len(corpus) >= min_samples`` to render a verdict.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# Candidate must beat incumbent by this margin before we hot-swap.
# Small enough that genuine SFT lifts land; large enough that baseline
# noise never trips promotion.
GATE_PROMOTE_DELTA = 0.02

# Below this the corpus is too thin to render a verdict worth acting on.
GATE_MIN_SAMPLES = 5

# Minimum per-task-family samples when corpus carries task_type metadata.
GATE_MIN_GROUP_SAMPLES = 2

# If any single task family regresses beyond this delta, block promotion.
GATE_MAX_GROUP_REGRESSION = 0.05

# Confidence interval z-score for fold-delta lower-bound check.
GATE_CONFIDENCE_Z = 1.96


EvaluatorFn = Callable[[str, List[Dict[str, Any]]], float]


@dataclass
class GateVerdict:
    """Structured verdict from a single ``ReplayGate.evaluate`` call."""

    passed: bool
    reason: str
    candidate_score: Optional[float] = None
    incumbent_score: Optional[float] = None
    delta: Optional[float] = None
    corpus_size: int = 0
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ReplayGate:
    """Evidence-based promotion gate for candidate DheeModels.

    The gate is deliberately dumb: load corpus, score both models, diff,
    compare to threshold. All the ML judgment lives in the injected
    evaluator; the gate just enforces the "must beat incumbent by delta"
    contract that keeps ``model_improved`` honest.
    """

    def __init__(
        self,
        corpus_dir: str,
        *,
        evaluator: Optional[EvaluatorFn] = None,
        min_samples: int = GATE_MIN_SAMPLES,
        promote_delta: float = GATE_PROMOTE_DELTA,
        min_group_samples: int = GATE_MIN_GROUP_SAMPLES,
        max_group_regression: float = GATE_MAX_GROUP_REGRESSION,
        confidence_z: float = GATE_CONFIDENCE_Z,
    ) -> None:
        self._corpus_dir = corpus_dir
        self._evaluator = evaluator
        self._min_samples = int(min_samples)
        self._promote_delta = float(promote_delta)
        self._min_group_samples = max(1, int(min_group_samples))
        self._max_group_regression = max(0.0, float(max_group_regression))
        self._confidence_z = max(0.0, float(confidence_z))

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def evaluate(
        self,
        candidate_model_path: str,
        incumbent_model_path: Optional[str] = None,
    ) -> GateVerdict:
        """Score ``candidate`` vs ``incumbent`` on the replay corpus."""
        if not candidate_model_path:
            return GateVerdict(
                passed=False, reason="no_candidate", corpus_size=0,
            )

        try:
            corpus = self._load_corpus()
        except FileNotFoundError:
            return GateVerdict(passed=False, reason="no_corpus")

        if len(corpus) < self._min_samples:
            return GateVerdict(
                passed=False,
                reason="insufficient_samples",
                corpus_size=len(corpus),
                metrics={"required": self._min_samples},
            )

        evaluator = self._resolve_evaluator()
        if evaluator is None:
            return GateVerdict(
                passed=False,
                reason="no_evaluator",
                corpus_size=len(corpus),
            )

        try:
            candidate_score = float(evaluator(candidate_model_path, corpus))
        except Exception as exc:
            logger.exception("ReplayGate: candidate evaluation crashed")
            return GateVerdict(
                passed=False,
                reason="evaluator_error",
                corpus_size=len(corpus),
                metrics={"error": f"{type(exc).__name__}: {exc}"},
            )

        incumbent_score: Optional[float]
        if incumbent_model_path:
            try:
                incumbent_score = float(
                    evaluator(incumbent_model_path, corpus)
                )
            except Exception as exc:
                logger.exception("ReplayGate: incumbent evaluation crashed")
                return GateVerdict(
                    passed=False,
                    reason="evaluator_error",
                    candidate_score=candidate_score,
                    corpus_size=len(corpus),
                    metrics={"error": f"{type(exc).__name__}: {exc}"},
                )
        else:
            # No incumbent means this is the first model ever — we still
            # refuse to auto-promote without a reference, to honor the
            # "no silent promotion" rule.
            return GateVerdict(
                passed=False,
                reason="no_incumbent",
                candidate_score=candidate_score,
                corpus_size=len(corpus),
            )

        delta = candidate_score - incumbent_score
        metrics: Dict[str, Any] = {
            "promote_delta": self._promote_delta,
            "min_group_samples": self._min_group_samples,
            "max_group_regression": self._max_group_regression,
        }

        # Group-relative safety checks: if task types are present in corpus
        # metadata, enforce minimum per-group support and block catastrophic
        # single-group regressions.
        groups = self._group_corpus_by_task_type(corpus)
        if groups:
            counts = {k: len(v) for k, v in groups.items()}
            metrics["group_counts"] = counts
            sparse = sorted(
                [k for k, n in counts.items() if n < self._min_group_samples]
            )
            if sparse:
                metrics["sparse_groups"] = sparse
                return GateVerdict(
                    passed=False,
                    reason="insufficient_group_samples",
                    candidate_score=candidate_score,
                    incumbent_score=incumbent_score,
                    delta=delta,
                    corpus_size=len(corpus),
                    metrics=metrics,
                )

            group_deltas: Dict[str, float] = {}
            for task_type, subset in groups.items():
                try:
                    cand_g = float(evaluator(candidate_model_path, subset))
                    base_g = float(evaluator(incumbent_model_path, subset))
                except Exception as exc:
                    logger.exception(
                        "ReplayGate: group evaluator crashed (task_type=%s)",
                        task_type,
                    )
                    return GateVerdict(
                        passed=False,
                        reason="evaluator_error",
                        candidate_score=candidate_score,
                        incumbent_score=incumbent_score,
                        delta=delta,
                        corpus_size=len(corpus),
                        metrics={
                            **metrics,
                            "error": f"{type(exc).__name__}: {exc}",
                            "task_type": task_type,
                        },
                    )
                group_deltas[task_type] = cand_g - base_g
            metrics["group_deltas"] = group_deltas
            if group_deltas:
                worst_group, worst_delta = min(
                    group_deltas.items(), key=lambda kv: kv[1]
                )
                metrics["worst_group"] = {
                    "task_type": worst_group,
                    "delta": worst_delta,
                }
                if worst_delta < -self._max_group_regression:
                    return GateVerdict(
                        passed=False,
                        reason="group_regression",
                        candidate_score=candidate_score,
                        incumbent_score=incumbent_score,
                        delta=delta,
                        corpus_size=len(corpus),
                        metrics=metrics,
                    )

        ci_lower = delta
        fold_deltas = self._fold_deltas(
            evaluator=evaluator,
            candidate_model_path=candidate_model_path,
            incumbent_model_path=incumbent_model_path,
            corpus=corpus,
        )
        if fold_deltas:
            metrics["fold_deltas"] = fold_deltas
        if len(fold_deltas) >= 2:
            mean_delta = sum(fold_deltas) / len(fold_deltas)
            if len(fold_deltas) > 1:
                variance = sum(
                    (d - mean_delta) ** 2 for d in fold_deltas
                ) / (len(fold_deltas) - 1)
            else:
                variance = 0.0
            stderr = math.sqrt(max(0.0, variance)) / math.sqrt(len(fold_deltas))
            ci_lower = mean_delta - self._confidence_z * stderr
            metrics["confidence"] = {
                "fold_count": len(fold_deltas),
                "mean_delta": mean_delta,
                "stderr": stderr,
                "z": self._confidence_z,
                "ci_lower": ci_lower,
            }

        passed = delta >= self._promote_delta and ci_lower >= self._promote_delta
        reason = "promoted" if passed else "regressed"
        if delta >= self._promote_delta and ci_lower < self._promote_delta:
            reason = "low_confidence"

        return GateVerdict(
            passed=passed,
            reason=reason,
            candidate_score=candidate_score,
            incumbent_score=incumbent_score,
            delta=delta,
            corpus_size=len(corpus),
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Corpus + evaluator plumbing
    # ------------------------------------------------------------------

    def _load_corpus(self) -> List[Dict[str, Any]]:
        if not self._corpus_dir or not os.path.isdir(self._corpus_dir):
            raise FileNotFoundError(self._corpus_dir)
        records: List[Dict[str, Any]] = []
        for name in sorted(os.listdir(self._corpus_dir)):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(self._corpus_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue
        return records

    def _resolve_evaluator(self) -> Optional[EvaluatorFn]:
        if self._evaluator is not None:
            return self._evaluator
        return _default_karma_evaluator()

    def _group_corpus_by_task_type(
        self, corpus: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for rec in corpus:
            task_type = self._task_type_from_record(rec)
            if not task_type:
                continue
            groups.setdefault(task_type, []).append(rec)
        return groups

    @staticmethod
    def _task_type_from_record(rec: Dict[str, Any]) -> str:
        meta = rec.get("metadata")
        if isinstance(meta, dict):
            value = meta.get("task_type")
            if isinstance(value, str) and value.strip():
                return value.strip()
        value = rec.get("task_type")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return ""

    def _fold_deltas(
        self,
        *,
        evaluator: EvaluatorFn,
        candidate_model_path: str,
        incumbent_model_path: str,
        corpus: List[Dict[str, Any]],
        fold_count: int = 5,
    ) -> List[float]:
        if len(corpus) < max(2, fold_count):
            return []
        folds: List[List[Dict[str, Any]]] = [[] for _ in range(max(2, fold_count))]
        for idx, rec in enumerate(corpus):
            folds[idx % len(folds)].append(rec)
        deltas: List[float] = []
        for subset in folds:
            if not subset:
                continue
            try:
                cand = float(evaluator(candidate_model_path, subset))
                base = float(evaluator(incumbent_model_path, subset))
            except Exception:
                # Keep CI best-effort; evaluator hard-fail is already handled
                # for the full corpus above.
                continue
            deltas.append(cand - base)
        return deltas


def _default_karma_evaluator() -> Optional[EvaluatorFn]:
    """Return the karma-based evaluator if its heavyweight deps import.

    Delegates to ``dhee.mini.karma_evaluator.build_karma_evaluator``,
    which honestly returns ``None`` when ``torch`` / ``transformers``
    aren't installed. In that case ReplayGate reports ``no_evaluator``
    rather than fabricating a plausible score.
    """
    try:
        from dhee.mini.karma_evaluator import build_karma_evaluator
    except Exception:
        return None
    return build_karma_evaluator()
