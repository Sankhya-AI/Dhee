"""Tests for engram-accel Rust acceleration layer.

Tests correctness of both the Rust implementation (if available) and the
pure-Python fallback. All tests must pass regardless of whether engram_accel
is installed.
"""

import math
import pytest
from unittest.mock import patch

from engram.utils.math import (
    cosine_similarity,
    cosine_similarity_batch,
    ACCEL_AVAILABLE,
    _pure_python_cosine,
)
from engram.core.retrieval import tokenize, bm25_score_batch


# ── cosine_similarity ───────────────────────────────────────────────────

class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert cosine_similarity([], []) == 0.0

    def test_mismatched_lengths(self):
        assert cosine_similarity([1, 2], [1, 2, 3]) == 0.0

    def test_zero_vector(self):
        assert cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0

    def test_none_input(self):
        assert cosine_similarity(None, [1, 2]) == 0.0
        assert cosine_similarity([1, 2], None) == 0.0

    def test_high_dimensional(self):
        """Test with 1024-dim vectors (typical embedding size)."""
        import random
        random.seed(42)
        a = [random.gauss(0, 1) for _ in range(1024)]
        b = [random.gauss(0, 1) for _ in range(1024)]
        result = cosine_similarity(a, b)
        expected = _pure_python_cosine(a, b)
        assert result == pytest.approx(expected, abs=1e-10)

    def test_parallel_vectors_different_magnitude(self):
        a = [1.0, 2.0, 3.0]
        b = [2.0, 4.0, 6.0]
        assert cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-6)

    def test_known_angle(self):
        """45-degree angle → cos(45°) ≈ 0.7071."""
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        expected = 1.0 / math.sqrt(2)
        assert cosine_similarity(a, b) == pytest.approx(expected, abs=1e-6)


# ── cosine_similarity_batch ─────────────────────────────────────────────

class TestCosineSimilarityBatch:
    def test_single_vector(self):
        query = [1.0, 0.0, 0.0]
        store = [[1.0, 0.0, 0.0]]
        result = cosine_similarity_batch(query, store)
        assert len(result) == 1
        assert result[0] == pytest.approx(1.0)

    def test_multiple_vectors(self):
        query = [1.0, 0.0]
        store = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]
        result = cosine_similarity_batch(query, store)
        assert len(result) == 3
        assert result[0] == pytest.approx(1.0)
        assert result[1] == pytest.approx(0.0)
        assert result[2] == pytest.approx(-1.0)

    def test_empty_store(self):
        result = cosine_similarity_batch([1.0, 0.0], [])
        assert result == []

    def test_empty_query(self):
        result = cosine_similarity_batch([], [[1.0, 0.0]])
        assert result == [0.0]

    def test_batch_matches_sequential(self):
        """Batch results must match per-vector cosine_similarity calls."""
        import random
        random.seed(123)
        query = [random.gauss(0, 1) for _ in range(256)]
        store = [[random.gauss(0, 1) for _ in range(256)] for _ in range(50)]

        batch_results = cosine_similarity_batch(query, store)
        sequential_results = [cosine_similarity(query, v) for v in store]

        for b, s in zip(batch_results, sequential_results):
            assert b == pytest.approx(s, abs=1e-10)

    def test_large_batch(self):
        """Test with 1000 vectors of 512 dims."""
        import random
        random.seed(456)
        query = [random.gauss(0, 1) for _ in range(512)]
        store = [[random.gauss(0, 1) for _ in range(512)] for _ in range(1000)]
        result = cosine_similarity_batch(query, store)
        assert len(result) == 1000
        # Verify all results are in valid range
        for r in result:
            assert -1.0 - 1e-10 <= r <= 1.0 + 1e-10


# ── tokenize ────────────────────────────────────────────────────────────

class TestTokenize:
    def test_basic(self):
        result = tokenize("Hello World")
        assert result == ["hello", "world"]

    def test_punctuation(self):
        result = tokenize("hello, world! how's it going?")
        assert "hello" in result
        assert "world" in result

    def test_empty(self):
        assert tokenize("") == []

    def test_numbers(self):
        result = tokenize("I have 42 apples")
        assert "42" in result
        assert "apples" in result


# ── bm25_score_batch ────────────────────────────────────────────────────

class TestBM25ScoreBatch:
    def test_basic_scoring(self):
        query_terms = ["hello", "world"]
        documents = [
            ["hello", "world", "foo"],
            ["bar", "baz"],
            ["hello", "hello", "hello"],
        ]
        scores = bm25_score_batch(query_terms, documents, 3, 3.0)
        assert len(scores) == 3
        # First doc matches both terms, should score highest
        assert scores[0] > scores[1]
        # Second doc has no matches
        assert scores[1] == 0.0
        # Third doc has only "hello", not "world"
        assert scores[2] > 0.0

    def test_empty_query(self):
        scores = bm25_score_batch([], [["hello"]], 1, 1.0)
        assert scores == [0.0]

    def test_empty_documents(self):
        scores = bm25_score_batch(["hello"], [], 0, 1.0)
        assert scores == []


# ── Fallback behavior ──────────────────────────────────────────────────

class TestFallback:
    def test_cosine_fallback_works(self):
        """Even without Rust, cosine_similarity should work."""
        result = _pure_python_cosine([1.0, 0.0], [1.0, 0.0])
        assert result == pytest.approx(1.0)

    def test_accel_flag_is_bool(self):
        assert isinstance(ACCEL_AVAILABLE, bool)


# ── Decay acceleration ─────────────────────────────────────────────────

class TestDecayAccel:
    def test_decay_import(self):
        """Ensure decay module loads without error."""
        from engram.core.decay import calculate_decayed_strength
        assert callable(calculate_decayed_strength)

    def test_traces_batch_import(self):
        """Ensure traces batch function is available."""
        from engram.core.traces import decay_traces_batch
        assert callable(decay_traces_batch)
