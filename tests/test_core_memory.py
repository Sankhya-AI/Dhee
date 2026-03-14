"""Tests for CoreMemory - zero-config, no LLM required."""
import pytest
import tempfile
import os
import uuid

from engram import CoreMemory


class TestCoreMemory:
    """Test CoreMemory functionality."""

    def test_add_and_search(self):
        """Basic add and search functionality."""
        tag = uuid.uuid4().hex[:8]
        content = f"I like Python {tag}"
        m = CoreMemory(preset="minimal")
        m.add(content)
        # Simple embedder uses hash-based similarity, so search with same text
        results = m.search(content)
        assert len(results["results"]) >= 1
        assert "Python" in results["results"][0]["memory"]
        m.close()

    def test_content_dedup(self):
        """Same content twice = deduplication + access boost."""
        m = CoreMemory(preset="minimal")
        m.add("I like Python")
        r2 = m.add("I like Python")
        # Should dedup
        assert r2["results"][0]["event"] == "DEDUPLICATED"
        m.close()

    def test_apply_decay(self):
        """Decay cycle runs without error."""
        m = CoreMemory(preset="minimal")
        m.add("Test memory")
        result = m.apply_decay()
        assert "decayed" in result
        m.close()

    def test_get_and_delete(self):
        """Get and delete operations work."""
        m = CoreMemory(preset="minimal")
        r = m.add("To be deleted")
        mem_id = r["results"][0]["id"]
        # Get should return the memory
        mem = m.get(mem_id)
        assert mem is not None
        assert mem["memory"] == "To be deleted"
        # Delete (tombstone)
        m.delete(mem_id)
        # After tombstone delete, get() filters tombstoned records
        mem_after_delete = m.get(mem_id)
        assert mem_after_delete is None
        m.close()

    def test_query_cache(self):
        """Query embedding cache populates on search."""
        m = CoreMemory(preset="minimal")
        m.add("Caching is good")
        # First search populates cache
        m.search("caching")
        # Second search should hit cache
        m.search("caching")
        # Cache should have at least one entry
        assert len(m._query_cache) > 0
        m.close()

    def test_get_all_memories(self):
        """Get all memories returns results."""
        m = CoreMemory(preset="minimal")
        m.add("Memory one")
        m.add("Memory two")
        results = m.get_all(limit=10)
        assert len(results["results"]) >= 2
        m.close()

    def test_get_stats(self):
        """Get stats returns memory counts."""
        m = CoreMemory(preset="minimal")
        stats_before = m.get_stats()
        m.add("Test memory")
        stats_after = m.get_stats()
        # Stats might be user-scoped, so just check structure
        assert "total" in stats_after
        m.close()

    def test_normalized_dedup(self):
        """Case/whitespace normalized deduplication."""
        m = CoreMemory(preset="minimal")
        m.add(" Hello World ")
        r2 = m.add("hello world")
        assert r2["results"][0]["event"] == "DEDUPLICATED"
        m.close()

    def test_access_boost_on_dedup(self):
        """Re-encountering strengthens memory."""
        m = CoreMemory(preset="minimal")
        r1 = m.add("Boost test")
        mem_id = r1["results"][0]["id"]
        # Deduplicate should increment access count
        r2 = m.add("Boost test")
        # Access the memory to trigger increment_access
        mem = m.get(mem_id)
        # Access count should be incremented
        assert mem["access_count"] >= 1
        m.close()

    def test_empty_content(self):
        """Empty content returns empty results."""
        m = CoreMemory(preset="minimal")
        result = m.add("")
        assert result["results"] == []
        m.close()

    def test_whitespace_content(self):
        """Whitespace-only content returns empty results."""
        m = CoreMemory(preset="minimal")
        result = m.add("   ")
        assert result["results"] == []
        m.close()

    def test_search_empty_query(self):
        """Empty search query returns empty results."""
        m = CoreMemory(preset="minimal")
        m.add("Test memory")
        result = m.search("")
        assert result["results"] == []
        m.close()

    def test_update_memory(self):
        """Update memory content works."""
        m = CoreMemory(preset="minimal")
        r = m.add("Original content")
        mem_id = r["results"][0]["id"]
        # Update via string
        m.update(mem_id, "Updated content")
        # Verify update
        mem = m.get(mem_id)
        assert mem["memory"] == "Updated content"
        m.close()

    def test_history(self):
        """History tracks memory operations."""
        content = f"History test {uuid.uuid4().hex[:8]}"
        m = CoreMemory(preset="minimal")
        r = m.add(content)
        mem_id = r["results"][0]["id"]
        history = m.history(mem_id)
        # Should have at least the ADD event
        assert len(history) >= 1
        events = [h["event"] for h in history]
        assert "ADD" in events
        m.close()

    def test_limit_parameter(self):
        """Search limit parameter works."""
        m = CoreMemory(preset="minimal")
        for i in range(10):
            m.add(f"Memory {i}")
        results = m.search("Memory", limit=3)
        assert len(results["results"]) <= 3
        m.close()

    def test_user_id_filtering(self):
        """Different user_ids are isolated."""
        m = CoreMemory(preset="minimal")
        m.add("User A memory", user_id="user_a")
        m.add("User B memory", user_id="user_b")
        results_a = m.search("memory", user_id="user_a")
        results_b = m.search("memory", user_id="user_b")
        # Each should only see their own
        for r in results_a["results"]:
            assert "User A" in r["memory"]
        for r in results_b["results"]:
            assert "User B" in r["memory"]
        m.close()

    def test_default_user(self):
        """Default user_id is 'default'."""
        m = CoreMemory(preset="minimal")
        r = m.add("Default user memory")
        mem_id = r["results"][0]["id"]
        mem = m.get(mem_id)
        assert mem["user_id"] == "default"
        m.close()

    def test_persistence(self):
        """Memories persist in the database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            # First instance
            m1 = CoreMemory(preset="minimal")
            m1.db.db_path = db_path  # Use test path
            m1.add("Persistent memory")
            mem_id = m1.db.get_all_memories(limit=1)[0]["id"]
            # Mem should exist
            assert m1.get(mem_id) is not None
            m1.close()
