"""Configuration for engram-procedural."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ProceduralConfig(BaseModel):
    """Configuration for the engram-procedural package."""

    min_episodes_for_extraction: int = Field(
        default=3, description="Episodes needed before extracting a procedure"
    )
    automaticity_threshold: int = Field(
        default=5, description="Uses before procedure gets automatic retrieval boost"
    )
    automaticity_boost: float = Field(
        default=0.20, description="Search boost for automatic procedures"
    )
    max_procedures_per_user: int = Field(
        default=500, description="Maximum procedures per user"
    )
    success_weight: float = Field(
        default=0.7, description="Weight of success rate in strength calculation"
    )
    abstraction_similarity: float = Field(
        default=0.80, description="Threshold for cross-domain abstraction"
    )
    extraction_prompt: Optional[str] = Field(
        default=None, description="Custom LLM prompt for procedure extraction"
    )

    @field_validator("min_episodes_for_extraction", "automaticity_threshold")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        return max(1, int(v))

    @field_validator("automaticity_boost", "success_weight", "abstraction_similarity")
    @classmethod
    def _clamp_unit_float(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))

    @field_validator("max_procedures_per_user")
    @classmethod
    def _clamp_max(cls, v: int) -> int:
        return min(10000, max(1, int(v)))
