"""Vector math — Rust-powered, no fallbacks."""

from typing import List, Optional
from dhee_accel import (
    cosine_similarity as _rs_cosine,
    cosine_similarity_batch as _rs_cosine_batch,
)

ACCEL_AVAILABLE = True


def cosine_similarity(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    """Compute cosine similarity between two vectors (Rust-accelerated)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return _rs_cosine(list(a), list(b))


def cosine_similarity_batch(
    query: List[float], store: List[List[float]]
) -> List[float]:
    """Compute cosine similarity of *query* against every vector in *store* (SIMD)."""
    if not query or not store:
        return [0.0] * len(store)
    return _rs_cosine_batch(list(query), [list(v) for v in store])
