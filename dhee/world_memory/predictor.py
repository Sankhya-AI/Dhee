from __future__ import annotations

import hashlib
from typing import Iterable, List


class ActionConditionedPredictor:
    """Deterministic stand-in for an action-conditioned world predictor."""

    def __init__(self, step_scale: float = 0.18):
        self.step_scale = max(0.01, float(step_scale))

    def predict(
        self,
        current_latent: List[float],
        action_type: str,
        action_payload: dict | None = None,
        action_trace: Iterable[str] | None = None,
    ) -> List[float]:
        payload = action_payload or {}
        trace = list(action_trace or [])
        key = f"{action_type}|{payload}|{trace}".encode("utf-8")
        digest = hashlib.sha256(key).digest()
        delta = []
        for idx, base in enumerate(current_latent):
            byte = digest[idx % len(digest)]
            signed = (byte / 255.0) * 2.0 - 1.0
            delta.append(base + signed * self.step_scale)
        return _normalize(delta)


def compute_surprise(predicted_next_latent: List[float], actual_next_latent: List[float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(predicted_next_latent, actual_next_latent)) ** 0.5


def _normalize(values: List[float]) -> List[float]:
    norm = sum(v * v for v in values) ** 0.5
    if norm <= 0.0:
        return [0.0 for _ in values]
    return [v / norm for v in values]
