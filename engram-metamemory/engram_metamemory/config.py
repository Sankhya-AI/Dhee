"""Configuration for engram-metamemory."""

from pydantic import BaseModel, Field, field_validator


class MetamemoryConfig(BaseModel):
    """Configuration for metamemory confidence and calibration."""

    # Confidence scoring weights (must sum to ~1.0)
    w_strength: float = Field(default=0.30, description="Weight for memory strength signal")
    w_echo_depth: float = Field(default=0.20, description="Weight for echo encoding depth")
    w_access_count: float = Field(default=0.15, description="Weight for retrieval frequency")
    w_recency: float = Field(default=0.15, description="Weight for time since creation")
    w_source: float = Field(default=0.20, description="Weight for source reliability")

    # Feeling of Knowing thresholds
    fok_confident_threshold: float = Field(default=0.7, description="Score above this = confident")
    fok_uncertain_threshold: float = Field(default=0.3, description="Score below this = unknown")

    # Knowledge gap settings
    max_gaps: int = Field(default=500, description="Maximum tracked gaps per user")
    gap_dedup_threshold: float = Field(default=0.85, description="Cosine sim for gap deduplication")

    # Calibration settings
    calibration_window: int = Field(default=100, description="Rolling window for calibration stats")

    # Multi-source confidence boost
    multi_source_boost: float = Field(default=0.10, description="Boost when multiple sources agree")
    max_multi_source_boost: float = Field(default=0.25, description="Maximum multi-source boost")

    # Recency half-life in days
    recency_half_life_days: float = Field(default=7.0, description="Days until recency factor halves")

    @field_validator(
        "w_strength", "w_echo_depth", "w_access_count", "w_recency", "w_source",
        "fok_confident_threshold", "fok_uncertain_threshold",
        "gap_dedup_threshold", "multi_source_boost", "max_multi_source_boost",
    )
    @classmethod
    def _clamp_unit_float(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))

    @field_validator("max_gaps", "calibration_window")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        return max(1, int(v))

    @field_validator("recency_half_life_days")
    @classmethod
    def _positive_float(cls, v: float) -> float:
        return max(0.1, float(v))
