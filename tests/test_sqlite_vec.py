"""Tests for sqlite-vec vector store implementation."""

import math
import os

import pytest

sqlite_vec = pytest.importorskip("sqlite_vec", reason="sqlite-vec not installed")

from dhee.vector_stores.base import MemoryResult
from dhee.vector_stores.sqlite_vec import SqliteVecStore


@pytest.fixture
def store(tmp_path):
    """Create a SqliteVecStore with a temporary database."""
    config = {
        "path": str(tmp_path / "vec_test.db"),
        "collection_name": "test_col",
        "embedding_model_dims": 4,
    }
    return SqliteVecStore(config)


def _norm(v):
    """Normalize a vector to unit length."""
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v] if mag > 0 else v


class TestInsert:
    def test_insert_single(self, store):
        store.insert(
            vectors=[[1.0, 0.0, 0.0, 0.0]],
            payloads=[{"text": "hello"}],
            ids=["id-1"],
        )
        result = store.get("id-1")
        assert result is not None
        assert result.id == "id-1"
        assert result.payload["text"] == "hello"

    def test_insert_multiple(self, store):
        store.insert(
            vectors=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
            payloads=[{"text": "a"}, {"text": "b"}],
            ids=["id-1", "id-2"],
        )
        assert store.get("id-1") is not None
        assert store.get("id-2") is not None

    def test_insert_auto_ids(self, store):
        store.insert(vectors=[[1.0, 0.0, 0.0, 0.0]])
        info = store.col_info()
        assert info["points"] == 1

    def test_insert_upsert(self, store):
        store.insert(
            vectors=[[1.0, 0.0, 0.0, 0.0]],
            payloads=[{"text": "old"}],
            ids=["id-1"],
        )
        store.insert(
            vectors=[[0.0, 1.0, 0.0, 0.0]],
            payloads=[{"text": "new"}],
            ids=["id-1"],
        )
        result = store.get("id-1")
        assert result.payload["text"] == "new"
        assert store.col_info()["points"] == 1

    def test_insert_mismatched_lengths(self, store):
        with pytest.raises(ValueError):
            store.insert(
                vectors=[[1.0, 0.0, 0.0, 0.0]],
                payloads=[{"a": 1}, {"b": 2}],
            )


class TestSearch:
    def test_cosine_similarity_ordering(self, store):
        v1 = _norm([1.0, 0.0, 0.0, 0.0])
        v2 = _norm([0.7, 0.7, 0.0, 0.0])
        v3 = _norm([0.0, 1.0, 0.0, 0.0])
        store.insert(
            vectors=[v1, v2, v3],
            payloads=[{"label": "exact"}, {"label": "partial"}, {"label": "orthogonal"}],
            ids=["a", "b", "c"],
        )
        query = _norm([1.0, 0.0, 0.0, 0.0])
        results = store.search(query=None, vectors=query, limit=3)
        assert len(results) == 3
        assert results[0].id == "a"  # Most similar
        assert results[0].score > results[1].score
        assert results[1].score > results[2].score

    def test_search_respects_limit(self, store):
        for i in range(10):
            store.insert(
                vectors=[_norm([float(i), 1.0, 0.0, 0.0])],
                ids=[f"id-{i}"],
            )
        results = store.search(query=None, vectors=_norm([5.0, 1.0, 0.0, 0.0]), limit=3)
        assert len(results) == 3

    def test_search_with_filters(self, store):
        store.insert(
            vectors=[_norm([1.0, 0.0, 0.0, 0.0]), _norm([1.0, 0.1, 0.0, 0.0])],
            payloads=[{"user_id": "alice"}, {"user_id": "bob"}],
            ids=["a", "b"],
        )
        results = store.search(
            query=None,
            vectors=_norm([1.0, 0.0, 0.0, 0.0]),
            limit=5,
            filters={"user_id": "alice"},
        )
        assert len(results) == 1
        assert results[0].payload["user_id"] == "alice"

    def test_search_empty_collection(self, store):
        results = store.search(query=None, vectors=[1.0, 0.0, 0.0, 0.0], limit=5)
        assert results == []


class TestDelete:
    def test_delete_existing(self, store):
        store.insert(
            vectors=[[1.0, 0.0, 0.0, 0.0]],
            ids=["id-1"],
        )
        store.delete("id-1")
        assert store.get("id-1") is None
        assert store.col_info()["points"] == 0

    def test_delete_nonexistent(self, store):
        store.delete("nonexistent")  # Should not raise


class TestUpdate:
    def test_update_payload(self, store):
        store.insert(
            vectors=[[1.0, 0.0, 0.0, 0.0]],
            payloads=[{"text": "old"}],
            ids=["id-1"],
        )
        store.update("id-1", payload={"text": "new"})
        result = store.get("id-1")
        assert result.payload["text"] == "new"

    def test_update_vector(self, store):
        v1 = _norm([1.0, 0.0, 0.0, 0.0])
        store.insert(vectors=[v1], ids=["id-1"])

        v2 = _norm([0.0, 1.0, 0.0, 0.0])
        store.update("id-1", vector=v2)

        # Search for the new vector direction should rank it first
        results = store.search(query=None, vectors=v2, limit=1)
        assert results[0].id == "id-1"

    def test_update_nonexistent(self, store):
        store.update("nonexistent", payload={"x": 1})  # Should not raise


class TestGet:
    def test_get_existing(self, store):
        store.insert(
            vectors=[[1.0, 0.0, 0.0, 0.0]],
            payloads=[{"key": "value"}],
            ids=["id-1"],
        )
        result = store.get("id-1")
        assert isinstance(result, MemoryResult)
        assert result.id == "id-1"
        assert result.payload["key"] == "value"

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent") is None


class TestCollectionOps:
    def test_list_cols(self, store):
        cols = store.list_cols()
        assert "test_col" in cols

    def test_col_info(self, store):
        store.insert(vectors=[[1.0, 0.0, 0.0, 0.0]], ids=["a"])
        store.insert(vectors=[[0.0, 1.0, 0.0, 0.0]], ids=["b"])
        info = store.col_info()
        assert info["name"] == "test_col"
        assert info["points"] == 2
        assert info["vector_size"] == 4

    def test_delete_col(self, store):
        store.insert(vectors=[[1.0, 0.0, 0.0, 0.0]], ids=["a"])
        store.delete_col()
        # After delete_col, list should be empty
        cols = store.list_cols()
        assert "test_col" not in cols

    def test_reset(self, store):
        store.insert(vectors=[[1.0, 0.0, 0.0, 0.0]], ids=["a"])
        store.reset()
        assert store.col_info()["points"] == 0
        # But collection exists again
        assert "test_col" in store.list_cols()


class TestList:
    def test_list_all(self, store):
        store.insert(
            vectors=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
            payloads=[{"label": "a"}, {"label": "b"}],
            ids=["id-1", "id-2"],
        )
        results = store.list()
        assert len(results) == 2

    def test_list_with_filters(self, store):
        store.insert(
            vectors=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
            payloads=[{"user_id": "alice"}, {"user_id": "bob"}],
            ids=["id-1", "id-2"],
        )
        results = store.list(filters={"user_id": "alice"})
        assert len(results) == 1
        assert results[0].payload["user_id"] == "alice"

    def test_list_with_limit(self, store):
        for i in range(10):
            store.insert(vectors=[[float(i), 0.0, 0.0, 0.0]], ids=[f"id-{i}"])
        results = store.list(limit=3)
        assert len(results) == 3
