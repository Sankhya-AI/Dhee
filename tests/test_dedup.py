"""Tests for content-hash deduplication and access boost."""

import uuid

from engram import CoreMemory


def _unique(prefix: str = "dedup") -> str:
    """Generate unique content to avoid cross-test collisions in shared DB."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TestContentDedup:
    def test_exact_match_dedup(self):
        content = _unique("exact")
        m = CoreMemory(preset="minimal")
        r1 = m.add(content)
        r2 = m.add(content)
        assert r2["results"][0]["event"] == "DEDUPLICATED"
        m.close()

    def test_normalized_dedup(self):
        """Case/whitespace normalized."""
        tag = _unique("norm")
        m = CoreMemory(preset="minimal")
        m.add(f"  {tag}  ")
        r2 = m.add(tag.lower())
        assert r2["results"][0]["event"] == "DEDUPLICATED"
        m.close()

    def test_access_boost_on_dedup(self):
        """Re-encountering strengthens memory."""
        content = _unique("boost")
        m = CoreMemory(preset="minimal")
        r1 = m.add(content)
        mem_id = r1["results"][0]["id"]
        # Deduplicate
        r2 = m.add(content)
        assert r2["results"][0]["event"] == "DEDUPLICATED"
        assert r2["results"][0]["id"] == mem_id
        # Access count should be incremented
        mem = m.get(mem_id)
        assert mem["access_count"] >= 1
        m.close()

    def test_different_content_no_dedup(self):
        """Different content should not deduplicate."""
        m = CoreMemory(preset="minimal")
        r1 = m.add(_unique("first"))
        r2 = m.add(_unique("second"))
        assert r1["results"][0]["event"] == "ADD"
        assert r2["results"][0]["event"] == "ADD"
        assert r1["results"][0]["id"] != r2["results"][0]["id"]
        m.close()

    def test_dedup_preserves_original_id(self):
        """Dedup returns the original memory's ID."""
        content = _unique("preserve")
        m = CoreMemory(preset="minimal")
        r1 = m.add(content)
        original_id = r1["results"][0]["id"]
        r2 = m.add(content)
        assert r2["results"][0]["id"] == original_id
        m.close()

    def test_dedup_across_users(self):
        """Same content for different users should NOT dedup."""
        content = _unique("shared")
        m = CoreMemory(preset="minimal")
        r1 = m.add(content, user_id=f"user_a_{uuid.uuid4().hex[:6]}")
        r2 = m.add(content, user_id=f"user_b_{uuid.uuid4().hex[:6]}")
        assert r1["results"][0]["event"] == "ADD"
        assert r2["results"][0]["event"] == "ADD"
        m.close()
