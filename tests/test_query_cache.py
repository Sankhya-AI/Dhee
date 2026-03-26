"""Tests for query embedding LRU cache."""

from dhee import CoreMemory


class TestQueryCache:
    def test_cache_populated(self):
        m = CoreMemory(preset="minimal")
        m.add("Cache test")
        m.search("cache")
        assert len(m._query_cache) == 1
        m.close()

    def test_cache_hit(self):
        """Same query returns cached embedding."""
        m = CoreMemory(preset="minimal")
        m.add("Unique content xyz123")
        m.search("content")
        cached_before = dict(m._query_cache)
        m.search("content")
        # Cache size should not grow (hit, not miss)
        assert len(m._query_cache) == len(cached_before)
        m.close()

    def test_different_queries_distinct_entries(self):
        """Different queries produce separate cache entries."""
        m = CoreMemory(preset="minimal")
        m.add("Some data to search")
        m.search("first query")
        m.search("second query")
        assert len(m._query_cache) == 2
        m.close()

    def test_cache_eviction(self):
        """Cache respects max size."""
        m = CoreMemory(preset="minimal")
        m._query_cache_max = 3  # small for testing
        m.add("Data for eviction test")
        for i in range(5):
            m.search(f"query {i}")
        assert len(m._query_cache) <= 3
        m.close()
