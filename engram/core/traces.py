"""Benna-Fusi inspired multi-timescale strength traces.

Each memory has three traces (fast, mid, slow) that decay at different rates
and cascade information from fast -> mid -> slow during sleep cycles.

Requires engram-accel (Rust) for batch decay operations.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Tuple

if TYPE_CHECKING:
    from engram.configs.base import DistillationConfig

try:
    from engram_accel import decay_traces_batch as _rs_decay_traces_batch
except ImportError:
    _rs_decay_traces_batch = None


def initialize_traces(
    strength: float, is_new: bool = True
) -> Tuple[float, float, float]:
    """Initialize (s_fast, s_mid, s_slow) for a memory.

    New memories: all strength in fast trace.
    Migrated memories: spread across fast and mid.
    """
    strength = max(0.0, min(1.0, float(strength)))
    if is_new:
        return (strength, 0.0, 0.0)
    return (strength, strength * 0.5, 0.0)


def compute_effective_strength(
    s_fast: float, s_mid: float, s_slow: float, config: "DistillationConfig"
) -> float:
    """Weighted combination of three traces into a single effective strength."""
    effective = (
        config.s_fast_weight * s_fast
        + config.s_mid_weight * s_mid
        + config.s_slow_weight * s_slow
    )
    return max(0.0, min(1.0, effective))


def decay_traces(
    s_fast: float,
    s_mid: float,
    s_slow: float,
    last_accessed: datetime,
    access_count: int,
    config: "DistillationConfig",
) -> Tuple[float, float, float]:
    """Decay each trace independently at its own rate."""
    if isinstance(last_accessed, str):
        last_accessed = datetime.fromisoformat(last_accessed)
    if last_accessed.tzinfo is None:
        last_accessed = last_accessed.replace(tzinfo=timezone.utc)

    elapsed_days = (datetime.now(timezone.utc) - last_accessed).total_seconds() / 86400.0
    dampening = 1.0 + 0.5 * math.log1p(access_count)

    new_fast = s_fast * math.exp(-config.s_fast_decay_rate * elapsed_days / dampening)
    new_mid = s_mid * math.exp(-config.s_mid_decay_rate * elapsed_days / dampening)
    new_slow = s_slow * math.exp(-config.s_slow_decay_rate * elapsed_days / dampening)

    return (
        max(0.0, min(1.0, new_fast)),
        max(0.0, min(1.0, new_mid)),
        max(0.0, min(1.0, new_slow)),
    )


def decay_traces_batch(
    traces: List[Tuple[float, float, float]],
    elapsed_days: List[float],
    access_counts: List[int],
    config: "DistillationConfig",
) -> List[Tuple[float, float, float]]:
    """Batch version of decay_traces (Rust-accelerated with Python fallback)."""
    if _rs_decay_traces_batch is not None:
        return _rs_decay_traces_batch(
            traces,
            elapsed_days,
            [int(a) for a in access_counts],
            config.s_fast_decay_rate,
            config.s_mid_decay_rate,
            config.s_slow_decay_rate,
        )
    # Python fallback
    results = []
    for (sf, sm, ss), ed, ac in zip(traces, elapsed_days, access_counts):
        dampening = 1.0 + 0.5 * math.log1p(ac)
        results.append((
            max(0.0, min(1.0, sf * math.exp(-config.s_fast_decay_rate * ed / dampening))),
            max(0.0, min(1.0, sm * math.exp(-config.s_mid_decay_rate * ed / dampening))),
            max(0.0, min(1.0, ss * math.exp(-config.s_slow_decay_rate * ed / dampening))),
        ))
    return results


def cascade_traces(
    s_fast: float,
    s_mid: float,
    s_slow: float,
    config: "DistillationConfig",
    deep_sleep: bool = False,
) -> Tuple[float, float, float]:
    """Transfer strength from faster traces to slower traces."""
    fast_to_mid = s_fast * config.cascade_fast_to_mid
    new_fast = s_fast - fast_to_mid
    new_mid = s_mid + fast_to_mid

    if deep_sleep:
        mid_to_slow = new_mid * config.cascade_mid_to_slow
        new_mid = new_mid - mid_to_slow
        new_slow = s_slow + mid_to_slow
    else:
        new_slow = s_slow

    return (
        max(0.0, min(1.0, new_fast)),
        max(0.0, min(1.0, new_mid)),
        max(0.0, min(1.0, new_slow)),
    )


def boost_fast_trace(s_fast: float, boost: float) -> float:
    """On access, only the fast trace gets boosted (not mid/slow)."""
    return max(0.0, min(1.0, s_fast + boost))
