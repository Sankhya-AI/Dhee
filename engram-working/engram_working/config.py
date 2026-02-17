"""Configuration for engram-working."""

from pydantic import BaseModel, Field, field_validator


class WorkingMemoryConfig(BaseModel):
    """Configuration for the engram-working package."""

    capacity: int = Field(
        default=7, description="Max items in working memory (Miller's Law)"
    )
    decay_minutes: float = Field(
        default=30.0, description="Minutes before an item's activation halves"
    )
    min_activation: float = Field(
        default=0.1, description="Activation below this triggers eviction"
    )
    auto_flush_to_longterm: bool = Field(
        default=True, description="Flush evicted items to long-term memory"
    )
    snapshot_enabled: bool = Field(
        default=False, description="Persist snapshots to DB for process restart recovery"
    )

    @field_validator("capacity")
    @classmethod
    def _clamp_capacity(cls, v: int) -> int:
        return min(20, max(1, int(v)))

    @field_validator("decay_minutes")
    @classmethod
    def _positive_float(cls, v: float) -> float:
        return max(1.0, float(v))

    @field_validator("min_activation")
    @classmethod
    def _clamp_activation(cls, v: float) -> float:
        return min(0.5, max(0.01, float(v)))
