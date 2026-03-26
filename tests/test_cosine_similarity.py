"""Tests for engram.utils.math cosine_similarity."""

import pytest
from dhee.utils.math import cosine_similarity


def test_identical_vectors():
    assert cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)


def test_orthogonal_vectors():
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)


def test_opposite_vectors():
    assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)


def test_empty_vectors():
    assert cosine_similarity([], []) == 0.0


def test_mismatched_lengths():
    assert cosine_similarity([1, 2], [1, 2, 3]) == 0.0


def test_zero_vector():
    assert cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0


def test_none_input():
    assert cosine_similarity(None, [1, 2]) == 0.0  # type: ignore[arg-type]
    assert cosine_similarity([1, 2], None) == 0.0  # type: ignore[arg-type]


def test_high_dimensional():
    """Test with 3072-dimensional vectors (typical embedding size)."""
    import random
    random.seed(42)
    a = [random.gauss(0, 1) for _ in range(3072)]
    b = list(a)  # identical copy
    assert cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-6)


def test_known_similarity():
    a = [1.0, 2.0, 3.0]
    b = [4.0, 5.0, 6.0]
    # Known cosine similarity
    import math
    dot = 1*4 + 2*5 + 3*6  # 32
    norm_a = math.sqrt(1 + 4 + 9)  # sqrt(14)
    norm_b = math.sqrt(16 + 25 + 36)  # sqrt(77)
    expected = dot / (norm_a * norm_b)
    assert cosine_similarity(a, b) == pytest.approx(expected, abs=1e-6)
