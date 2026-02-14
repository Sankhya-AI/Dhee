"""Shared math utilities for engram.

This module is the single canonical source for cosine similarity and related
numerical operations. All other modules should import from here.

Requires engram-accel (Rust) for SIMD-optimized operations.
"""

from typing import List, Optional

import math as _math

try:
    from engram_accel import (
        cosine_similarity as _rs_cosine,
        cosine_similarity_batch as _rs_cosine_batch,
    )
    ACCEL_AVAILABLE = True
except ImportError:
    ACCEL_AVAILABLE = False

    def _rs_cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = _math.sqrt(sum(x * x for x in a))
        nb = _math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    def _rs_cosine_batch(query, store):
        return [_rs_cosine(query, v) for v in store]


def _pure_python_cosine(a, b):
    """Pure Python cosine similarity (reference implementation for tests)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = _math.sqrt(sum(x * x for x in a))
    nb = _math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


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
