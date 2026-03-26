"""Configuration for teaching primitives."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class TeachingConfig(BaseModel):
    """Configuration for the teaching memory subsystem."""

    enable_teaching: bool = False  # Off by default (backward compat)
    concept_namespace: str = "sensai_curriculum"
    mastery_initial_score: float = 0.35
    mastery_increment: float = 0.08
    mastery_decrement_on_misconception: float = -0.15
    weak_concept_threshold: float = 0.45
    mastered_concept_threshold: float = 0.70

    @field_validator(
        "mastery_initial_score",
        "mastery_increment",
        "weak_concept_threshold",
        "mastered_concept_threshold",
    )
    @classmethod
    def _clamp_unit_float(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))
