"""FadeMem decay calculations.

Requires engram-accel (Rust) for the core decay math.
"""

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engram.configs.base import FadeMemConfig

try:
    from engram_accel import calculate_decayed_strength as _rs_decay
except ImportError:
    def _rs_decay(strength, elapsed_days, decay_rate, access_count, dampening_factor):
        dampening = 1.0 + dampening_factor * math.log1p(access_count)
        return strength * math.exp(-decay_rate * elapsed_days / dampening)


def calculate_decayed_strength(
    current_strength: float,
    last_accessed: datetime,
    access_count: int,
    layer: str,
    config: "FadeMemConfig",
) -> float:
    if isinstance(last_accessed, str):
        last_accessed = datetime.fromisoformat(last_accessed)
    if last_accessed.tzinfo is None:
        last_accessed = last_accessed.replace(tzinfo=timezone.utc)

    if math.isnan(current_strength):
        return 0.0

    time_elapsed_days = (datetime.now(timezone.utc) - last_accessed).total_seconds() / 86400.0
    decay_rate = config.sml_decay_rate if layer == "sml" else config.lml_decay_rate

    return _rs_decay(
        current_strength,
        time_elapsed_days,
        decay_rate,
        access_count,
        config.access_dampening_factor,
    )


def should_forget(strength: float, config: "FadeMemConfig") -> bool:
    if math.isnan(strength):
        return True
    return strength < config.forgetting_threshold


def should_promote(layer: str, access_count: int, strength: float, config: "FadeMemConfig") -> bool:
    if layer != "sml":
        return False
    return access_count >= config.promotion_access_threshold and strength >= config.promotion_strength_threshold
