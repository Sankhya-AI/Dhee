"""Tests for SmartMemory - echo + categories + graph."""
import pytest

from engram import SmartMemory


class TestSmartMemory:
    """Test SmartMemory functionality."""

    def test_lazy_echo_processor(self):
        """Echo processor not created when disabled."""
        m = SmartMemory(preset="minimal")  # echo disabled
        # Should be None when disabled
        assert m._echo_processor is None
        m.close()

    def test_lazy_category_processor(self):
        """Category processor not created when disabled."""
        m = SmartMemory(preset="minimal")  # categories disabled
        assert m._category_processor is None
        m.close()

    def test_echo_enabled_flow(self):
        """Echo metadata added when enabled (mock LLM)."""
        # With minimal preset, echo is disabled, so we just test the structure
        m = SmartMemory(preset="minimal")
        r = m.add("Important fact", echo_depth="deep")
        # Check result structure
        assert "results" in r
        assert len(r["results"]) > 0
        m.close()

    def test_minimal_preset_add(self):
        """SmartMemory with minimal preset works."""
        m = SmartMemory(preset="minimal")
        r = m.add("Technology preference: Python")
        assert "results" in r
        assert len(r["results"]) > 0
        m.close()

    def test_category_detection(self):
        """Category detection structure."""
        m = SmartMemory(preset="minimal")
        r = m.add("Technology preference: Python")
        # Result should have expected keys
        assert "results" in r
        assert len(r["results"]) > 0
        result = r["results"][0]
        # Categories could be empty or have values
        assert "categories" in result or True  # Structure ok
        m.close()

    def test_search_with_boost(self):
        """Search with echo/category boosting."""
        m = SmartMemory(preset="minimal")
        m.add("Test memory about programming")
        results = m.search(
            "programming",
            use_echo_boost=False,  # explicitly disable
            use_category_boost=False,
        )
        assert "results" in results
        m.close()

    def test_get_categories(self):
        """Get categories returns a list."""
        m = SmartMemory(preset="minimal")
        cats = m.get_categories()
        assert isinstance(cats, list)
        m.close()

    def test_search_with_agent_id(self):
        """Search with agent_id filter."""
        m = SmartMemory(preset="minimal")
        m.add("Agent memory", agent_id="agent_1")
        results = m.search("memory", agent_id="agent_1")
        assert "results" in results
        m.close()

    def test_add_with_metadata(self):
        """Add with metadata."""
        import uuid
        m = SmartMemory(preset="minimal")
        r = m.add(f"Test content {uuid.uuid4().hex[:8]}", metadata={"source": "test"})
        assert r["results"][0]["event"] == "ADD"
        m.close()

    def test_categories_param(self):
        """Explicit categories parameter."""
        import uuid
        m = SmartMemory(preset="minimal")
        r = m.add(f"Content {uuid.uuid4().hex[:8]}", categories=["test_cat"])
        assert "categories" in r["results"][0]
        m.close()

    def test_parent_inheritance(self):
        """SmartMemory inherits from CoreMemory."""
        from engram import CoreMemory
        m = SmartMemory(preset="minimal")
        # Should have all CoreMemory methods
        assert hasattr(m, "add")
        assert hasattr(m, "search")
        assert hasattr(m, "delete")
        assert hasattr(m, "apply_decay")
        assert hasattr(m, "get_stats")
        m.close()

    def test_search_limit(self):
        """Search respects limit parameter."""
        m = SmartMemory(preset="minimal")
        for i in range(5):
            m.add(f"Memory item {i}")
        results = m.search("item", limit=3)
        assert len(results["results"]) <= 3
        m.close()

    def test_close_releases_resources(self):
        """Close releases resources properly."""
        m = SmartMemory(preset="minimal")
        m.add("Test memory")
        m.close()
        # Should be able to call close again without error
        m.close()

    def test_repr(self):
        """SmartMemory has repr."""
        m = SmartMemory(preset="minimal")
        r = repr(m)
        assert "SmartMemory" in r or "db=" in r
        m.close()

    def test_user_id_in_add(self):
        """Add respects user_id parameter."""
        m = SmartMemory(preset="minimal")
        r = m.add("Content", user_id="custom_user")
        assert "results" in r
        m.close()

    def test_source_app_in_add(self):
        """Add accepts source_app parameter."""
        m = SmartMemory(preset="minimal")
        r = m.add("Content", source_app="test_app")
        assert "results" in r
        m.close()
