"""Pure confidence computation functions for metamemory.

These are stateless functions that can be called from engram core (main.py)
via guarded import, or from the Metamemory class directly.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from engram_metamemory.config import MetamemoryConfig

# Echo depth to numeric score mapping
_ECHO_DEPTH_SCORES = {
    "deep": 1.0,
    "medium": 0.6,
    "shallow": 0.3,
}

# Source type reliability scores
_SOURCE_SCORES = {
    "explicit_remember": 1.0,
    "user_stated": 0.9,
    "observed": 0.7,
    "inferred": 0.5,
    "mcp": 0.7,
    "cli": 0.6,
}


def compute_confidence(
    metadata: Dict[str, Any],
    strength: float = 1.0,
    access_count: int = 0,
    created_at: Optional[str] = None,
    config: Optional[MetamemoryConfig] = None,
) -> float:
    """Compute a confidence score (0.0-1.0) for a memory.

    This is the core metamemory signal. It combines multiple factors:
    - Memory strength (from FadeMem decay)
    - Echo encoding depth (deeper = more confident)
    - Access frequency (more retrieved = more validated)
    - Recency (newer memories are slightly more confident)
    - Source reliability (explicit user statements > inferences)

    Args:
        metadata: Memory metadata dict.
        strength: Current memory strength (0.0-1.0).
        access_count: Number of times this memory has been retrieved.
        created_at: ISO datetime string of creation time.
        config: MetamemoryConfig for weight tuning.

    Returns:
        Confidence score between 0.0 and 1.0.
    """
    cfg = config or MetamemoryConfig()

    # Factor 1: Strength signal (already 0-1)
    f_strength = min(1.0, max(0.0, float(strength)))

    # Factor 2: Echo depth signal
    echo_depth = metadata.get("echo_depth", "shallow")
    f_echo = _ECHO_DEPTH_SCORES.get(echo_depth, 0.3)

    # Factor 3: Access count signal (logarithmic, caps at ~1.0 around 20 accesses)
    f_access = min(1.0, math.log1p(access_count) / math.log1p(20))

    # Factor 4: Recency signal (exponential decay with half-life)
    f_recency = _compute_recency_factor(created_at, cfg.recency_half_life_days)

    # Factor 5: Source reliability
    f_source = _compute_source_factor(metadata)

    # Weighted combination
    score = (
        cfg.w_strength * f_strength
        + cfg.w_echo_depth * f_echo
        + cfg.w_access_count * f_access
        + cfg.w_recency * f_recency
        + cfg.w_source * f_source
    )

    return min(1.0, max(0.0, score))


def propagate_confidence(
    parent_confidence: float,
    relationship: str = "derived",
) -> float:
    """Compute confidence for a derived memory based on its parent.

    Args:
        parent_confidence: Confidence of the source memory.
        relationship: How this memory relates to parent.

    Returns:
        Propagated confidence score.
    """
    decay_factors = {
        "derived": 0.85,
        "inferred": 0.65,
        "summarized": 0.90,
        "contradicted": 0.30,
    }
    factor = decay_factors.get(relationship, 0.75)
    return min(1.0, max(0.0, parent_confidence * factor))


def multi_source_boost(
    base_confidence: float,
    source_count: int,
    config: Optional[MetamemoryConfig] = None,
) -> float:
    """Boost confidence when multiple independent sources agree.

    Args:
        base_confidence: Current confidence score.
        source_count: Number of corroborating sources.
        config: MetamemoryConfig for boost parameters.

    Returns:
        Boosted confidence score.
    """
    if source_count <= 1:
        return base_confidence

    cfg = config or MetamemoryConfig()
    boost = min(
        cfg.max_multi_source_boost,
        cfg.multi_source_boost * (source_count - 1),
    )
    return min(1.0, base_confidence + boost)


def _compute_recency_factor(
    created_at: Optional[str],
    half_life_days: float,
) -> float:
    """Compute recency factor with exponential decay."""
    if not created_at:
        return 0.5  # Unknown age = neutral

    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - created).total_seconds() / 86400
        # Exponential decay: factor = 2^(-age/half_life)
        return math.pow(2, -age_days / half_life_days)
    except (ValueError, TypeError):
        return 0.5


def _compute_source_factor(metadata: Dict[str, Any]) -> float:
    """Compute source reliability factor from metadata."""
    # Check for explicit remember
    if metadata.get("explicit_remember") or metadata.get("policy_repeated"):
        return _SOURCE_SCORES["explicit_remember"]

    source_type = metadata.get("source_type", "mcp")
    return _SOURCE_SCORES.get(source_type, 0.5)
