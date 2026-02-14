"""Benchmark tests for engram-accel Rust acceleration.

Run with: pytest tests/test_accel_benchmark.py -v --benchmark-only
(Requires pytest-benchmark to be installed.)
"""

import random
import pytest

from engram.utils.math import (
    cosine_similarity,
    cosine_similarity_batch,
    _pure_python_cosine,
    ACCEL_AVAILABLE,
)

# Deterministic seed for reproducible benchmarks
random.seed(42)

# Pre-generate test data
DIMS = 1024
QUERY_VEC = [random.gauss(0, 1) for _ in range(DIMS)]
STORE_100 = [[random.gauss(0, 1) for _ in range(DIMS)] for _ in range(100)]
STORE_1K = [[random.gauss(0, 1) for _ in range(DIMS)] for _ in range(1000)]


try:
    import pytest_benchmark  # noqa: F401
    HAS_BENCHMARK = True
except ImportError:
    HAS_BENCHMARK = False


@pytest.mark.skipif(not HAS_BENCHMARK, reason="pytest-benchmark not installed")
class TestBenchmarks:
    def test_cosine_single_pair(self, benchmark):
        a = QUERY_VEC
        b = STORE_100[0]
        benchmark(cosine_similarity, a, b)

    def test_cosine_python_fallback(self, benchmark):
        a = QUERY_VEC
        b = STORE_100[0]
        benchmark(_pure_python_cosine, a, b)

    def test_cosine_batch_100(self, benchmark):
        benchmark(cosine_similarity_batch, QUERY_VEC, STORE_100)

    def test_cosine_batch_1K(self, benchmark):
        benchmark(cosine_similarity_batch, QUERY_VEC, STORE_1K)

    def test_cosine_sequential_100(self, benchmark):
        def sequential():
            return [cosine_similarity(QUERY_VEC, v) for v in STORE_100]
        benchmark(sequential)
