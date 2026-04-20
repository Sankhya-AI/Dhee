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
    ) -> None:
        self._corpus_dir = corpus_dir
        self._evaluator = evaluator
        self._min_samples = int(min_samples)
        self._promote_delta = float(promote_delta)

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
        passed = delta >= self._promote_delta

        return GateVerdict(
            passed=passed,
            reason="promoted" if passed else "regressed",
            candidate_score=candidate_score,
            incumbent_score=incumbent_score,
            delta=delta,
            corpus_size=len(corpus),
            metrics={"promote_delta": self._promote_delta},
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
