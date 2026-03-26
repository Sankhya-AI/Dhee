"""Tests for backward compatibility — existing Memory operations still work."""

import os
import tempfile
import uuid

import pytest

from dhee.db.sqlite import SQLiteManager


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = SQLiteManager(path)
    yield mgr
    os.unlink(path)


class TestMemoryBackwardCompat:
    """Existing memory CRUD operations should work unchanged."""

    def test_add_memory(self, db):
        mem_id = db.add_memory({
            "memory": "The user prefers dark mode",
            "user_id": "default",
        })
        assert mem_id
        mem = db.get_memory(mem_id)
        assert mem is not None
        assert mem["memory"] == "The user prefers dark mode"

    def test_update_memory(self, db):
        mem_id = db.add_memory({
            "memory": "old content",
            "user_id": "default",
        })
        success = db.update_memory(mem_id, {"memory": "new content"})
        assert success
        mem = db.get_memory(mem_id)
        assert mem["memory"] == "new content"

    def test_delete_memory(self, db):
        mem_id = db.add_memory({
            "memory": "to delete",
            "user_id": "default",
        })
        db.delete_memory(mem_id)
        mem = db.get_memory(mem_id)
        assert mem is None  # Tombstoned

    def test_get_all_memories(self, db):
        db.add_memory({"memory": "mem1", "user_id": "u1"})
        db.add_memory({"memory": "mem2", "user_id": "u1"})
        db.add_memory({"memory": "mem3", "user_id": "u2"})

        u1_mems = db.get_all_memories(user_id="u1")
        assert len(u1_mems) == 2

        all_mems = db.get_all_memories()
        assert len(all_mems) == 3

    def test_increment_access(self, db):
        mem_id = db.add_memory({"memory": "test", "user_id": "default"})
        db.increment_access(mem_id)
        mem = db.get_memory(mem_id)
        assert mem["access_count"] == 1

    def test_categories_still_work(self, db):
        cat_id = db.save_category({
            "id": "cat1",
            "name": "preferences",
            "description": "User preferences",
        })
        assert cat_id == "cat1"
        cat = db.get_category("cat1")
        assert cat["name"] == "preferences"

    def test_history(self, db):
        mem_id = db.add_memory({"memory": "test", "user_id": "default"})
        history = db.get_history(mem_id)
        assert len(history) >= 1
        assert history[0]["event"] == "ADD"

    def test_decay_log(self, db):
        db.log_decay(5, 2, 1)
        # Should not raise

    def test_memory_with_scene_id(self, db):
        """Memories can now have a scene_id column."""
        mem_id = db.add_memory({"memory": "test", "user_id": "default"})
        db.update_memory(mem_id, {"scene_id": "scene-123"})
        mem = db.get_memory(mem_id)
        assert mem.get("scene_id") == "scene-123"


class TestNewTablesCoexist:
    """New tables should not interfere with existing operations."""

    def test_scenes_empty_by_default(self, db):
        scenes = db.get_scenes()
        assert scenes == []

    def test_profiles_empty_by_default(self, db):
        profiles = db.get_all_profiles()
        assert profiles == []

    def test_scene_crud(self, db):
        scene_id = db.add_scene({
            "user_id": "u1",
            "title": "Test Scene",
            "topic": "testing",
            "start_time": "2024-01-01T00:00:00",
        })
        scene = db.get_scene(scene_id)
        assert scene["title"] == "Test Scene"

        db.update_scene(scene_id, {"title": "Updated Scene"})
        scene = db.get_scene(scene_id)
        assert scene["title"] == "Updated Scene"

    def test_profile_crud(self, db):
        profile_id = db.add_profile({
            "user_id": "u1",
            "name": "Alice",
            "profile_type": "contact",
            "facts": ["Works at Google"],
        })
        profile = db.get_profile(profile_id)
        assert profile["name"] == "Alice"
        assert "Works at Google" in profile["facts"]

        db.update_profile(profile_id, {"facts": ["Works at Google", "Likes Python"]})
        profile = db.get_profile(profile_id)
        assert len(profile["facts"]) == 2

    def test_scene_memory_junction(self, db):
        mem_id = db.add_memory({"memory": "linked mem", "user_id": "u1"})
        scene_id = db.add_scene({
            "user_id": "u1",
            "title": "Scene",
            "start_time": "2024-01-01T00:00:00",
        })
        db.add_scene_memory(scene_id, mem_id, position=0)

        scene_mems = db.get_scene_memories(scene_id)
        assert len(scene_mems) == 1
        assert scene_mems[0]["id"] == mem_id

    def test_profile_memory_junction(self, db):
        mem_id = db.add_memory({"memory": "about alice", "user_id": "u1"})
        profile_id = db.add_profile({
            "user_id": "u1",
            "name": "Alice",
            "profile_type": "contact",
        })
        db.add_profile_memory(profile_id, mem_id, role="mentioned")

        profile_mems = db.get_profile_memories(profile_id)
        assert len(profile_mems) == 1
        assert profile_mems[0]["id"] == mem_id
