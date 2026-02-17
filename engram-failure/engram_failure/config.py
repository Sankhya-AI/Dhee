"""Configuration for engram-failure."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class FailureConfig(BaseModel):
    """Configuration for the engram-failure package."""

    min_failures_for_antipattern: int = Field(
        default=3, description="Failures needed before extracting an anti-pattern"
    )
    max_failures_per_user: int = Field(
        default=1000, description="Maximum failure records per user"
    )
    auto_extract_antipatterns: bool = Field(
        default=True, description="Automatically extract anti-patterns from failure clusters"
    )
    similarity_threshold: float = Field(
        default=0.80, description="Similarity threshold for grouping related failures"
    )
    extraction_prompt: Optional[str] = Field(
        default=None, description="Custom LLM prompt for anti-pattern extraction"
    )

    @field_validator("min_failures_for_antipattern")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        return max(1, int(v))

    @field_validator("similarity_threshold")
    @classmethod
    def _clamp_unit_float(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))
