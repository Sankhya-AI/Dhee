"""Tests for SceneProcessor — boundary detection, creation, closing, summarization."""

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from dhee.core.scene import SceneProcessor, SceneDetectionResult, _detect_location, _cosine_similarity
from dhee.db.sqlite import SQLiteManager


@pytest.fixture
def db():
    """Create a temporary SQLite database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = SQLiteManager(path)
    yield mgr
    os.unlink(path)


@pytest.fixture
def processor(db):
    return SceneProcessor(
        db=db,
        embedder=None,
        llm=None,
        config={
            "scene_time_gap_minutes": 30,
            "scene_topic_threshold": 0.55,
            "auto_close_inactive_minutes": 120,
            "max_scene_memories": 5,
            "use_llm_summarization": False,  # No LLM in tests
        },
    )


class TestCosineSimililarity:
    def test_identical(self):
        v = [1.0, 0.0, 0.5]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_empty(self):
        assert _cosine_similarity([], []) == 0.0

    def test_different_lengths(self):
        assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0


class TestLocationDetection:
    def test_at_location(self):
        loc = _detect_location("Meeting at Starbucks")
        assert loc is not None
        assert "Starbucks" in loc

    def test_in_location(self):
        assert _detect_location("Currently in New York") == "New York"

    def test_no_location(self):
        assert _detect_location("just a random sentence") is None


class TestBoundaryDetection:
    def test_no_current_scene(self, processor):
        result = processor.detect_boundary("hello", datetime.now(timezone.utc).isoformat(), None)
        assert result.is_new_scene is True
        assert result.reason == "no_scene"

    def test_time_gap(self, processor):
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(minutes=60)).isoformat()
        scene = {"start_time": old_time, "end_time": old_time, "memory_ids": ["a"]}
        result = processor.detect_boundary("hi", now.isoformat(), scene)
        assert result.is_new_scene is True
        assert result.reason == "time_gap"

    def test_no_gap(self, processor):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(minutes=5)).isoformat()
        scene = {
            "start_time": recent,
            "end_time": recent,
            "memory_ids": ["a"],
            "location": None,
            "embedding": None,
        }
        result = processor.detect_boundary("hi", now.isoformat(), scene)
        assert result.is_new_scene is False

    def test_max_memories(self, processor):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(minutes=1)).isoformat()
        scene = {
            "start_time": recent,
            "end_time": recent,
            "memory_ids": ["a", "b", "c", "d", "e"],  # max is 5
            "location": None,
            "embedding": None,
        }
        result = processor.detect_boundary("hi", now.isoformat(), scene)
        assert result.is_new_scene is True
        assert result.reason == "max_memories"

    def test_topic_shift(self, processor):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(minutes=1)).isoformat()
        # Orthogonal embeddings = similarity 0
        scene_emb = [1.0, 0.0, 0.0]
        mem_emb = [0.0, 1.0, 0.0]
        scene = {
            "start_time": recent,
            "end_time": recent,
            "memory_ids": ["a"],
            "location": None,
            "embedding": scene_emb,
        }
        result = processor.detect_boundary("hi", now.isoformat(), scene, embedding=mem_emb)
        assert result.is_new_scene is True
        assert result.reason == "topic_shift"

    def test_location_change(self, processor):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(minutes=1)).isoformat()
        scene = {
            "start_time": recent,
            "end_time": recent,
            "memory_ids": ["a"],
            "location": "Office",
            "embedding": None,
        }
        result = processor.detect_boundary("Meeting at Starbucks today", now.isoformat(), scene)
        assert result.is_new_scene is True
        assert result.reason == "location_change"


class TestSceneLifecycle:
    def test_create_scene(self, processor, db):
        mem_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        # Add a memory first
        db.add_memory({"id": mem_id, "memory": "test", "user_id": "u1"})

        scene = processor.create_scene(
            first_memory_id=mem_id,
            user_id="u1",
            timestamp=now,
            topic="Test topic",
            location="Office",
        )
        assert scene["id"]
        assert scene["topic"] == "Test topic"
        assert scene["memory_ids"] == [mem_id]

        # Verify in DB
        fetched = db.get_scene(scene["id"])
        assert fetched is not None
        assert fetched["user_id"] == "u1"

    def test_add_memory_to_scene(self, processor, db):
        mem1 = str(uuid.uuid4())
        mem2 = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.add_memory({"id": mem1, "memory": "first", "user_id": "u1"})
        db.add_memory({"id": mem2, "memory": "second", "user_id": "u1"})

        scene = processor.create_scene(mem1, "u1", now, topic="topic")
        processor.add_memory_to_scene(scene["id"], mem2, timestamp=now)

        fetched = db.get_scene(scene["id"])
        assert mem2 in fetched["memory_ids"]

    def test_close_scene(self, processor, db):
        mem_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.add_memory({"id": mem_id, "memory": "test", "user_id": "u1"})

        scene = processor.create_scene(mem_id, "u1", now, topic="topic")
        assert db.get_open_scene("u1") is not None

        processor.close_scene(scene["id"])
        fetched = db.get_scene(scene["id"])
        assert fetched["end_time"] is not None

    def test_get_open_scene(self, processor, db):
        mem_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.add_memory({"id": mem_id, "memory": "test", "user_id": "u1"})

        processor.create_scene(mem_id, "u1", now, topic="t1")
        open_scene = db.get_open_scene("u1")
        assert open_scene is not None


class TestSceneSearch:
    def test_keyword_search(self, processor, db):
        mem_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.add_memory({"id": mem_id, "memory": "test", "user_id": "u1"})

        processor.create_scene(mem_id, "u1", now, topic="python debugging session")
        processor.close_scene(
            db.get_open_scene("u1")["id"],
            timestamp=now,
        )
        # Update summary manually since no LLM
        scenes = db.get_scenes(user_id="u1")
        db.update_scene(scenes[0]["id"], {"summary": "Debugging Python code"})

        results = processor.search_scenes("python", user_id="u1")
        assert len(results) >= 1
