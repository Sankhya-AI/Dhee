"""Configuration for engram-prospective."""

from pydantic import BaseModel, Field, field_validator


class ProspectiveConfig(BaseModel):
    """Configuration for prospective memory (intentions)."""

    # Limits
    max_intentions_per_user: int = Field(default=200, description="Maximum active intentions per user")

    # Decay for abandoned intentions
    intention_decay_days: float = Field(default=30.0, description="Days before unchecked intentions start decaying")
    intention_expiry_days: float = Field(default=90.0, description="Days before expired intentions are auto-cancelled")

    # Priority settings
    default_priority: int = Field(default=5, description="Default priority (1=highest, 10=lowest)")
    high_priority_threshold: int = Field(default=3, description="Priority <= this is considered high")

    # Trigger evaluation
    time_tolerance_seconds: int = Field(default=300, description="Seconds of tolerance for time triggers (5min default)")

    @field_validator("max_intentions_per_user")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        return max(1, int(v))

    @field_validator("default_priority", "high_priority_threshold")
    @classmethod
    def _clamp_priority(cls, v: int) -> int:
        return min(10, max(1, int(v)))

    @field_validator("intention_decay_days", "intention_expiry_days")
    @classmethod
    def _positive_float(cls, v: float) -> float:
        return max(1.0, float(v))

    @field_validator("time_tolerance_seconds")
    @classmethod
    def _clamp_tolerance(cls, v: int) -> int:
        return max(0, min(3600, int(v)))
