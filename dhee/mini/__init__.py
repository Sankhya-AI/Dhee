"""Dhee mini — tiny, DheeModel-local training orchestration.

The ``mini`` package is deliberately narrow: it owns only the
self-evolution training surface (``ProgressiveTrainer`` et al.). Anything
memory-layer-native lives in ``dhee.core`` / ``dhee.memory`` instead.
"""

from dhee.mini.progressive_trainer import (
    ProgressiveResult,
    ProgressiveTrainer,
    Stage,
)
from dhee.mini.replay_gate import (
    GATE_MIN_SAMPLES,
    GATE_PROMOTE_DELTA,
    GateVerdict,
    ReplayGate,
)
from dhee.mini.karma_evaluator import build_karma_evaluator

__all__ = [
    "ProgressiveResult",
    "ProgressiveTrainer",
    "Stage",
    "ReplayGate",
    "GateVerdict",
    "GATE_PROMOTE_DELTA",
    "GATE_MIN_SAMPLES",
    "build_karma_evaluator",
]
