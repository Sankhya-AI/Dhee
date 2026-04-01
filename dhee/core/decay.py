"""FadeMem decay calculations.

Uses dhee-accel (Rust) when available, pure-Python fallback otherwise.
"""

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dhee.configs.base import FadeMemConfig

try:
    from dhee_accel import calculate_decayed_strength as _rs_decay
    _ACCEL = True
except ImportError:
    _ACCEL = False


def _py_decay(
    strength: float,
    elapsed_days: float,
    decay_rate: float,
    access_count: int,
    dampening_factor: float,
) -> float:
    """Pure-Python decay: strength * exp(-rate * days / dampening)."""
    if math.isnan(strength):
        return 0.0
    dampening = 1.0 + dampening_factor * math.log(1.0 + access_count)
    decayed = strength * math.exp(-decay_rate * elapsed_days / dampening)
    return max(0.0, min(1.0, decayed))


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

    if _ACCEL:
        return _rs_decay(
            current_strength,
            time_elapsed_days,
            decay_rate,
            access_count,
            config.access_dampening_factor,
        )
    return _py_decay(
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
