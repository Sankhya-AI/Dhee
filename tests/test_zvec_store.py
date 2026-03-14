"""Tests for zvec vector store implementation."""

import math
import os

import pytest

zvec = pytest.importorskip("zvec", reason="zvec not installed")

from engram.vector_stores.base import MemoryResult
from engram.vector_stores.zvec_store import ZvecStore, _build_filter_string


@pytest.fixture
def store(tmp_path):
    """Create a ZvecStore with a temporary directory."""
    config = {
        "path": str(tmp_path / "zvec_test"),
        "collection_name": "test_col",
        "embedding_model_dims": 4,
    }
    return ZvecStore(config)


def _norm(v):
    """Normalize a vector to unit length."""
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v] if mag > 0 else v


class TestFilterString:
    def test_builds_promoted_fields(self):
        result = _build_filter_string({"user_id": "alice", "agent_id": "bot1"})
        assert "user_id == 'alice'" in result
        assert "agent_id == 'bot1'" in result

    def test_ignores_non_promoted(self):
        result = _build_filter_string({"custom_field": "val"})
        assert result is None

    def test_mixed_fields(self):
        result = _build_filter_string({"user_id": "alice", "custom": "val"})
        assert "user_id == 'alice'" in result
        assert "custom" not in result


class TestInsert:
    def test_insert_single(self, store):
        store.insert(
            vectors=[_norm([1.0, 0.0, 0.0, 0.0])],
            payloads=[{"text": "hello", "user_id": "default"}],
            ids=["id-1"],
        )
        result = store.get("id-1")
        assert result is not None
        assert result.id == "id-1"
        assert result.payload["text"] == "hello"

    def test_insert_multiple(self, store):
        store.insert(
            vectors=[_norm([1.0, 0.0, 0.0, 0.0]), _norm([0.0, 1.0, 0.0, 0.0])],
            payloads=[{"text": "a"}, {"text": "b"}],
            ids=["id-1", "id-2"],
        )
        assert store.get("id-1") is not None
        assert store.get("id-2") is not None

    def test_insert_validates_dimensions(self, store):
        with pytest.raises(ValueError, match="dimensions"):
            store.insert(vectors=[[1.0, 0.0]], payloads=[{}], ids=["bad"])

    def test_insert_validates_lengths(self, store):
        with pytest.raises(ValueError, match="payloads length"):
            store.insert(
                vectors=[_norm([1.0, 0.0, 0.0, 0.0])],
                payloads=[{}, {}],
            )

    def test_upsert_semantics(self, store):
        """Re-inserting same ID updates the record."""
        store.insert(
            vectors=[_norm([1.0, 0.0, 0.0, 0.0])],
            payloads=[{"text": "original"}],
            ids=["id-1"],
        )
        store.insert(
            vectors=[_norm([0.0, 1.0, 0.0, 0.0])],
            payloads=[{"text": "updated"}],
            ids=["id-1"],
        )
        result = store.get("id-1")
        assert result is not None
        assert result.payload["text"] == "updated"


class TestSearch:
    def test_search_returns_results(self, store):
        v1 = _norm([1.0, 0.0, 0.0, 0.0])
        v2 = _norm([0.0, 1.0, 0.0, 0.0])
        store.insert(
            vectors=[v1, v2],
            payloads=[{"text": "hello", "user_id": "default"}, {"text": "world", "user_id": "default"}],
            ids=["id-1", "id-2"],
        )
        results = store.search(query=None, vectors=v1, limit=2)
        assert len(results) >= 1
        # First result should be the closest match
        assert results[0].id == "id-1"
        assert results[0].score > 0

    def test_search_respects_limit(self, store):
        vectors = [_norm([float(i), 0.0, 0.0, 0.0]) for i in range(1, 6)]
        store.insert(
            vectors=vectors,
            payloads=[{"n": i} for i in range(5)],
        )
        results = store.search(query=None, vectors=vectors[0], limit=2)
        assert len(results) <= 2

    def test_search_with_filter(self, store):
        v1 = _norm([1.0, 0.0, 0.0, 0.0])
        v2 = _norm([1.0, 0.1, 0.0, 0.0])
        store.insert(
            vectors=[v1, v2],
            payloads=[
                {"text": "a", "user_id": "alice"},
                {"text": "b", "user_id": "bob"},
            ],
            ids=["id-a", "id-b"],
        )
        results = store.search(
            query=None, vectors=v1, limit=5, filters={"user_id": "alice"}
        )
        assert all(r.payload.get("user_id") == "alice" for r in results)

    def test_search_empty_collection(self, store):
        results = store.search(
            query=None, vectors=_norm([1.0, 0.0, 0.0, 0.0]), limit=5
        )
        assert results == []


class TestDelete:
    def test_delete_removes_record(self, store):
        store.insert(
            vectors=[_norm([1.0, 0.0, 0.0, 0.0])],
            payloads=[{"text": "bye"}],
            ids=["id-del"],
        )
        assert store.get("id-del") is not None
        store.delete("id-del")
        assert store.get("id-del") is None

    def test_delete_nonexistent_is_noop(self, store):
        store.delete("nonexistent")


class TestUpdate:
    def test_update_payload(self, store):
        v = _norm([1.0, 0.0, 0.0, 0.0])
        store.insert(vectors=[v], payloads=[{"text": "old"}], ids=["id-up"])
        store.update("id-up", payload={"text": "new"})
        result = store.get("id-up")
        assert result.payload["text"] == "new"

    def test_update_nonexistent_is_noop(self, store):
        store.update("nonexistent", payload={"text": "x"})


class TestCollectionOps:
    def test_list_cols(self, store):
        cols = store.list_cols()
        assert "test_col" in cols

    def test_col_info(self, store):
        info = store.col_info()
        assert info["name"] == "test_col"
        assert info["vector_size"] == 4

    def test_delete_col(self, store):
        store.delete_col()
        # After deletion, the collection directory should be gone
        col_path = store._collection_path("test_col")
        assert not os.path.exists(col_path)

    def test_reset(self, store):
        store.insert(
            vectors=[_norm([1.0, 0.0, 0.0, 0.0])],
            payloads=[{"text": "x"}],
            ids=["id-r"],
        )
        store.reset()
        assert store.get("id-r") is None

    def test_list_with_filters(self, store):
        store.insert(
            vectors=[_norm([1.0, 0.0, 0.0, 0.0]), _norm([0.0, 1.0, 0.0, 0.0])],
            payloads=[
                {"user_id": "alice", "text": "a"},
                {"user_id": "bob", "text": "b"},
            ],
            ids=["id-a", "id-b"],
        )
        results = store.list(filters={"user_id": "alice"})
        assert all(r.payload.get("user_id") == "alice" for r in results)


class TestClose:
    def test_close_prevents_operations(self, store):
        store.close()
        with pytest.raises(RuntimeError, match="closed"):
            store.search(query=None, vectors=[1.0, 0.0, 0.0, 0.0], limit=1)
