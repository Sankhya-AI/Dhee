"""Vector math — Rust-accelerated with pure-Python fallback."""

import math
from typing import List, Optional

try:
    from dhee_accel import (
        cosine_similarity as _rs_cosine,
        cosine_similarity_batch as _rs_cosine_batch,
    )
    ACCEL_AVAILABLE = True
except ImportError:
    ACCEL_AVAILABLE = False


def _py_cosine(a: List[float], b: List[float]) -> float:
    """Pure-Python cosine similarity."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    denom = norm_a * norm_b
    if denom == 0.0:
        return 0.0
    result = dot / denom
    if math.isnan(result) or math.isinf(result):
        return 0.0
    return result


def cosine_similarity(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    if ACCEL_AVAILABLE:
        return _rs_cosine(list(a), list(b))
    return _py_cosine(list(a), list(b))


def cosine_similarity_batch(
    query: List[float], store: List[List[float]]
) -> List[float]:
    """Compute cosine similarity of *query* against every vector in *store*."""
    if not query or not store:
        return [0.0] * len(store)
    if ACCEL_AVAILABLE:
        return _rs_cosine_batch(list(query), [list(v) for v in store])
    return [_py_cosine(list(query), list(v)) if len(v) == len(query) else 0.0 for v in store]
