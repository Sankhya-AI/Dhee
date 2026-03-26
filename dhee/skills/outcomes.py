"""OutcomeTracker — track skill success/failure and update confidence scores.

Confidence uses a Bayesian-inspired update with asymmetric weighting:
failures penalize more (weight 0.15) than successes reward (weight 0.10).
This ensures skills must prove themselves before reaching high confidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dhee.skills.schema import Skill
from dhee.skills.store import SkillStore

logger = logging.getLogger(__name__)

# Asymmetric weights: failures penalize more than successes reward
SUCCESS_WEIGHT = 0.10
FAILURE_WEIGHT = 0.15


@dataclass
class StepOutcome:
    """Granular outcome for a single step within a skill execution."""

    step_index: int = 0
    success: bool = True
    failure_type: Optional[str] = None  # "structural" | "slot"
    failed_slot: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "success": self.success,
            "failure_type": self.failure_type,
            "failed_slot": self.failed_slot,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepOutcome":
        return cls(
            step_index=int(data.get("step_index", 0)),
            success=data.get("success", True),
            failure_type=data.get("failure_type"),
            failed_slot=data.get("failed_slot"),
            notes=data.get("notes"),
        )


def compute_confidence(success_count: int, fail_count: int) -> float:
    """Compute Bayesian-inspired confidence score.

    Uses asymmetric weighting so failures penalize more than successes reward.
    Returns value in [0.0, 1.0].
    """
    total = success_count + fail_count
    if total == 0:
        return 0.5  # Prior: neutral confidence for new skills

    # Weighted success rate with asymmetric penalties
    weighted_success = success_count * SUCCESS_WEIGHT
    weighted_fail = fail_count * FAILURE_WEIGHT
    weighted_total = weighted_success + weighted_fail

    if weighted_total == 0:
        return 0.5

    raw = weighted_success / weighted_total
    # Regularize toward 0.5 for low sample sizes
    regularization = 1.0 / (1.0 + total * 0.1)
    confidence = raw * (1 - regularization) + 0.5 * regularization

    return max(0.0, min(1.0, confidence))


class OutcomeTracker:
    """Tracks skill outcomes and updates confidence scores."""

    def __init__(self, skill_store: SkillStore):
        self._store = skill_store

    def log_outcome(
        self,
        skill_id: str,
        success: bool,
        notes: Optional[str] = None,
        step_outcomes: Optional[List[StepOutcome]] = None,
    ) -> Dict[str, Any]:
        """Log a skill outcome and update confidence.

        If step_outcomes are provided and the skill has structure,
        per-step confidence/counts are updated too.

        Returns updated skill stats.
        """
        skill = self._store.get(skill_id)
        if skill is None:
            return {"error": f"Skill not found: {skill_id}"}

        # Update counts
        if success:
            skill.success_count += 1
        else:
            skill.fail_count += 1

        # Granular per-step feedback
        step_updates = []
        if step_outcomes and skill.structure is not None:
            from dhee.skills.structure import SkillStructure
            structure = SkillStructure.from_dict(skill.structure)
            for so in step_outcomes:
                if 0 <= so.step_index < len(structure.structured_steps):
                    step = structure.structured_steps[so.step_index]
                    if so.success:
                        step.success_count += 1
                    else:
                        step.fail_count += 1
                    step.confidence = compute_confidence(step.success_count, step.fail_count)
                    step_updates.append({
                        "step_index": so.step_index,
                        "template": step.template,
                        "new_confidence": round(step.confidence, 4),
                    })
            skill.structure = structure.to_dict()

        # Recompute confidence
        old_confidence = skill.confidence
        skill.confidence = compute_confidence(skill.success_count, skill.fail_count)
        skill.updated_at = datetime.now(timezone.utc).isoformat()

        # Persist
        self._store.save(skill)

        result = {
            "skill_id": skill.id,
            "skill_name": skill.name,
            "success": success,
            "old_confidence": round(old_confidence, 4),
            "new_confidence": round(skill.confidence, 4),
            "success_count": skill.success_count,
            "fail_count": skill.fail_count,
            "notes": notes,
        }
        if step_updates:
            result["step_updates"] = step_updates
        return result
