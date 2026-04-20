"""कर्म (Karma) — Multi-axis evaluation for DheeModel training.

A single loss metric is blind. It cannot distinguish a model that extracts
facts perfectly but anchors context wrong, from one that anchors context
but hallucinates facts. The karma vector can.

Eight axes — each measures a different dimension of extraction quality.
Between curriculum phases (lives), karma determines what knowledge survives.

Adapted from SamsaraNet's KarmaVector: the axes are remapped from RL
(intent, competence, consequence) to structured extraction
(fact accuracy, context accuracy, temporal reasoning).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class KarmaAxis(IntEnum):
    """Eight dimensions of extraction quality.

    SamsaraNet mapped these to RL (intent, competence, consequence).
    DheeModel maps them to structured extraction quality.
    The principle is the same: multi-dimensional evaluation reveals
    what single metrics hide.
    """

    FACT_PRECISION = 0     # Are extracted facts correct? (precision)
    FACT_RECALL = 1        # Are all facts captured? (recall)
    CONTEXT_ACCURACY = 2   # Is the context anchor right? (era, place, time)
    TEMPORAL_REASONING = 3 # Can it derive dates from chains?
    ENTITY_LINKING = 4     # Does it capture entity relationships?
    TASK_BALANCE = 5       # Is it equally good across all 6 tasks?
    GENERALIZATION = 6     # Does validation match training? (no overfitting)
    RETENTION = 7          # Does it remember previous curriculum phases?


KARMA_AXIS_NAMES = [
    "fact_precision",
    "fact_recall",
    "context_accuracy",
    "temporal_reasoning",
    "entity_linking",
    "task_balance",
    "generalization",
    "retention",
]


@dataclass
class KarmaVector:
    """Multi-axis quality residue from a curriculum phase (life).

    Values in [-1.0, 1.0]. Positive = good, negative = degraded.
    """

    values: np.ndarray = field(
        default_factory=lambda: np.zeros(8, dtype=np.float32)
    )

    def __getitem__(self, axis: KarmaAxis | int) -> float:
        return float(self.values[int(axis)])

    def __setitem__(self, axis: KarmaAxis | int, value: float) -> None:
        self.values[int(axis)] = np.clip(value, -1.0, 1.0)

    @property
    def net(self) -> float:
        return float(np.mean(self.values))

    def copy(self) -> KarmaVector:
        return KarmaVector(values=self.values.copy())

    def to_dict(self) -> Dict[str, float]:
        return {
            KARMA_AXIS_NAMES[i]: float(self.values[i]) for i in range(8)
        }

    def __repr__(self) -> str:
        parts = [
            f"{KARMA_AXIS_NAMES[i]}={self.values[i]:+.3f}" for i in range(8)
        ]
        return f"Karma({', '.join(parts)})"


@dataclass
class PhaseJudgment:
    """Yama's verdict on a curriculum phase.

    Determines what survives into the next phase:
    - Which task adapters are retained vs reinitialized
    - How much of the mid-trace is preserved
    - What training data is curated for the next phase
    """

    phase_name: str
    karma: KarmaVector
    strengths: List[str]       # axes where karma > 0.5
    weaknesses: List[str]      # axes where karma < -0.3
    unresolved: List[str]      # tasks that didn't meet threshold
    verdict: str               # "ascend" | "repeat" | "remediate"
    train_loss: float = 0.0
    val_loss: float = 0.0
    task_scores: Dict[str, float] = field(default_factory=dict)

    def should_ascend(self) -> bool:
        """Can the model proceed to the next curriculum phase?"""
        return self.verdict == "ascend"


class YamaEvaluator:
    """Evaluates a curriculum phase and produces a judgment.

    SamsaraNet's YamaEvaluator judged RL lives by reward, karma trajectory,
    and dharma alignment. DheeModel's Yama judges by extraction quality
    across all 6 task types.
    """

    def __init__(
        self,
        ascend_threshold: float = 0.3,
        weakness_threshold: float = -0.3,
        strength_threshold: float = 0.5,
    ):
        self.ascend_threshold = ascend_threshold
        self.weakness_threshold = weakness_threshold
        self.strength_threshold = strength_threshold

    def evaluate(
        self,
        phase_name: str,
        task_scores: Dict[str, float],
        train_loss: float,
        val_loss: float,
        prev_task_scores: Optional[Dict[str, float]] = None,
    ) -> PhaseJudgment:
        """Evaluate a completed curriculum phase.

        Args:
            phase_name: Name of the phase (e.g., "simple_facts")
            task_scores: Accuracy per task type {task_name: accuracy}
            train_loss: Final training loss
            val_loss: Final validation loss
            prev_task_scores: Scores from previous phase (for retention check)

        Returns:
            PhaseJudgment with karma vector and verdict
        """
        karma = KarmaVector()

        # --- Compute each karma axis ---

        # FACT_PRECISION: average accuracy of fact extraction tasks
        fact_tasks = [
            s for t, s in task_scores.items()
            if t in ("engram", "context", "scene")
        ]
        if fact_tasks:
            precision = np.mean(fact_tasks)
            karma[KarmaAxis.FACT_PRECISION] = 2.0 * precision - 1.0  # [0,1] -> [-1,1]

        # FACT_RECALL: penalize if any task has very low score
        if fact_tasks:
            min_score = min(fact_tasks)
            karma[KarmaAxis.FACT_RECALL] = 2.0 * min_score - 1.0

        # CONTEXT_ACCURACY: context task score specifically
        if "context" in task_scores:
            karma[KarmaAxis.CONTEXT_ACCURACY] = 2.0 * task_scores["context"] - 1.0
        elif "engram" in task_scores:
            karma[KarmaAxis.CONTEXT_ACCURACY] = 2.0 * task_scores["engram"] - 1.0

        # TEMPORAL_REASONING: answer + decompose tasks (require temporal inference)
        temporal_tasks = [
            s for t, s in task_scores.items()
            if t in ("answer", "decompose")
        ]
        if temporal_tasks:
            karma[KarmaAxis.TEMPORAL_REASONING] = 2.0 * np.mean(temporal_tasks) - 1.0

        # ENTITY_LINKING: engram task (which includes entity extraction)
        if "engram" in task_scores:
            karma[KarmaAxis.ENTITY_LINKING] = 2.0 * task_scores["engram"] - 1.0

        # TASK_BALANCE: std dev across task scores (low std = balanced = good)
        if len(task_scores) > 1:
            scores_arr = np.array(list(task_scores.values()))
            std = float(np.std(scores_arr))
            # std of 0.0 -> karma 1.0, std of 0.5 -> karma -1.0
            karma[KarmaAxis.TASK_BALANCE] = 1.0 - 4.0 * std
            karma[KarmaAxis.TASK_BALANCE] = np.clip(
                karma[KarmaAxis.TASK_BALANCE], -1.0, 1.0
            )

        # GENERALIZATION: train/val gap (small gap = good)
        if train_loss > 0 and val_loss > 0:
            gap = (val_loss - train_loss) / max(train_loss, 1e-6)
            # gap of 0 -> karma 1.0, gap of 1.0 -> karma -1.0
            karma[KarmaAxis.GENERALIZATION] = 1.0 - 2.0 * min(gap, 1.0)
        else:
            karma[KarmaAxis.GENERALIZATION] = 0.0

        # RETENTION: compare current scores to previous phase scores
        if prev_task_scores:
            retained_tasks = set(task_scores.keys()) & set(prev_task_scores.keys())
            if retained_tasks:
                retention_scores = []
                for task in retained_tasks:
                    # If current >= previous, retention is perfect (1.0)
                    # If current < previous, retention degrades
                    if prev_task_scores[task] > 0:
                        ratio = task_scores[task] / prev_task_scores[task]
                        retention_scores.append(min(ratio, 1.0))
                avg_retention = np.mean(retention_scores)
                karma[KarmaAxis.RETENTION] = 2.0 * avg_retention - 1.0

        # --- Determine verdict ---
        strengths = [
            KARMA_AXIS_NAMES[i]
            for i in range(8)
            if karma[i] >= self.strength_threshold
        ]
        weaknesses = [
            KARMA_AXIS_NAMES[i]
            for i in range(8)
            if karma[i] <= self.weakness_threshold
        ]

        # Tasks below minimum threshold
        unresolved = [
            task for task, score in task_scores.items()
            if score < 0.5
        ]

        # Verdict
        if karma.net >= self.ascend_threshold and not unresolved:
            verdict = "ascend"
        elif karma.net >= 0.0:
            verdict = "repeat"  # borderline — run this phase again
        else:
            verdict = "remediate"  # serious weakness — targeted remediation

        judgment = PhaseJudgment(
            phase_name=phase_name,
            karma=karma,
            strengths=strengths,
            weaknesses=weaknesses,
            unresolved=unresolved,
            verdict=verdict,
            train_loss=train_loss,
            val_loss=val_loss,
            task_scores=dict(task_scores),
        )

        logger.info(
            "Phase '%s' judgment: %s (net karma: %.3f). %s",
            phase_name,
            verdict.upper(),
            karma.net,
            karma,
        )

        return judgment
