"""Configuration for engram-reconsolidation."""

from pydantic import BaseModel, Field, field_validator


class ReconsolidationConfig(BaseModel):
    """Configuration for the engram-reconsolidation package."""

    min_confidence_for_auto_apply: float = Field(
        default=0.8, description="Auto-apply proposals above this confidence"
    )
    min_confidence_for_proposal: float = Field(
        default=0.5, description="Don't create proposals below this confidence"
    )
    cooldown_hours: float = Field(
        default=1.0, description="Min hours between reconsolidations of same memory"
    )
    max_versions: int = Field(
        default=50, description="Max version entries per memory"
    )
    require_conflict_check: bool = Field(
        default=True, description="Route updates through conflict resolution"
    )

    @field_validator("min_confidence_for_auto_apply", "min_confidence_for_proposal")
    @classmethod
    def _clamp_unit_float(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))

    @field_validator("cooldown_hours")
    @classmethod
    def _positive_float(cls, v: float) -> float:
        return max(0.0, float(v))

    @field_validator("max_versions")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        return max(1, min(1000, int(v)))
