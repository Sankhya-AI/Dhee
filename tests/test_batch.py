"""Tests for batch memory operations.

Tests correctness of batch add, batch echo, batch embed, batch category,
and batch DB insert. Verifies fallback behavior on failure.
"""

import os
import tempfile
import pytest

from engram.configs.base import BatchConfig, MemoryConfig
from engram.memory.main import Memory


def _make_memory(tmpdir, batch_enabled=True, echo_enabled=True, categories_enabled=True):
    """Create a Memory instance configured for testing with batch support."""
    config = MemoryConfig(
        vector_store={"provider": "memory", "config": {}},
        llm={"provider": "mock", "config": {}},
        embedder={"provider": "simple", "config": {}},
        history_db_path=os.path.join(tmpdir, "test.db"),
        graph={"enable_graph": False},
        scene={"enable_scenes": False},
        profile={"enable_profiles": False},
        handoff={"enable_handoff": False},
        echo={"enable_echo": echo_enabled},
        category={"enable_categories": categories_enabled, "use_llm_categorization": False},
        batch=BatchConfig(enable_batch=batch_enabled, max_batch_size=10),
    )
    return Memory(config)


class TestBatchConfig:
    def test_defaults(self):
        config = BatchConfig()
        assert config.enable_batch is False
        assert config.max_batch_size == 20
        assert config.batch_echo is True
        assert config.batch_embed is True
        assert config.batch_category is True

    def test_in_memory_config(self):
        config = MemoryConfig()
        assert hasattr(config, "batch")
        assert config.batch.enable_batch is False

    def test_max_batch_size_clamped(self):
        config = BatchConfig(max_batch_size=0)
        assert config.max_batch_size == 1
        config = BatchConfig(max_batch_size=200)
        assert config.max_batch_size == 100


class TestAddBatch:
    def test_basic_batch_add(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_memory(tmpdir)
            items = [
                {"content": "User likes Python"},
                {"content": "User works at Acme Corp"},
                {"content": "User prefers dark mode"},
            ]
            result = m.add_batch(items, user_id="test_user")
            assert "results" in result
            assert len(result["results"]) == 3
            for r in result["results"]:
                assert r["event"] == "ADD"
                assert r["id"]
                assert r["memory"]
            m.close()

    def test_batch_with_disabled_config_falls_back(self):
        """When batch is disabled, add_batch falls back to sequential add()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_memory(tmpdir, batch_enabled=False)
            items = [
                {"content": "Fact one"},
                {"content": "Fact two"},
            ]
            result = m.add_batch(items, user_id="test_user")
            assert "results" in result
            assert len(result["results"]) == 2
            m.close()

    def test_empty_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_memory(tmpdir)
            result = m.add_batch([], user_id="test_user")
            assert result == {"results": []}
            m.close()

    def test_batch_produces_searchable_memories(self):
        """Memories added via batch should be searchable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_memory(tmpdir, echo_enabled=False, categories_enabled=False)
            items = [
                {"content": "User loves hiking in mountains"},
                {"content": "User is allergic to peanuts"},
            ]
            m.add_batch(items, user_id="test_user")

            # Search should find the memories
            search_result = m.search("hiking", user_id="test_user")
            assert len(search_result["results"]) > 0
            m.close()

    def test_batch_respects_max_batch_size(self):
        """Items exceeding max_batch_size are split into chunks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_memory(tmpdir, echo_enabled=False, categories_enabled=False)
            # max_batch_size is 10, so 15 items should be split into 2 chunks
            items = [{"content": f"Fact number {i}"} for i in range(15)]
            result = m.add_batch(items, user_id="test_user")
            assert len(result["results"]) == 15
            m.close()

    def test_batch_with_per_item_metadata(self):
        """Each item can have its own metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_memory(tmpdir, echo_enabled=False, categories_enabled=False)
            items = [
                {"content": "Item A", "metadata": {"source": "email"}},
                {"content": "Item B", "metadata": {"source": "chat"}},
            ]
            result = m.add_batch(items, user_id="test_user")
            assert len(result["results"]) == 2
            m.close()

    def test_batch_with_categories(self):
        """Items can have per-item categories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_memory(tmpdir, echo_enabled=False)
            items = [
                {"content": "I prefer Python", "categories": ["preferences"]},
                {"content": "Meeting at 3pm", "categories": ["context"]},
            ]
            result = m.add_batch(items, user_id="test_user")
            assert len(result["results"]) == 2
            assert result["results"][0]["categories"] == ["preferences"]
            assert result["results"][1]["categories"] == ["context"]
            m.close()


class TestBatchDBInsert:
    def test_batch_insert_creates_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_memory(tmpdir, echo_enabled=False, categories_enabled=False)
            items = [{"content": f"Memory {i}"} for i in range(5)]
            m.add_batch(items, user_id="test_user")

            # Verify all 5 are in DB
            all_mems = m.get_all(user_id="test_user", limit=100)
            assert len(all_mems["results"]) == 5
            m.close()


class TestEmbedBatch:
    def test_embed_batch_default_fallback(self):
        """BaseEmbedder.embed_batch defaults to sequential."""
        from engram.embeddings.base import BaseEmbedder

        class DummyEmbedder(BaseEmbedder):
            def embed(self, text, memory_action=None):
                return [float(len(text))]

        embedder = DummyEmbedder()
        results = embedder.embed_batch(["hello", "world"])
        assert len(results) == 2
        assert results[0] == [5.0]
        assert results[1] == [5.0]

    def test_embed_batch_empty(self):
        from engram.embeddings.base import BaseEmbedder

        class DummyEmbedder(BaseEmbedder):
            def embed(self, text, memory_action=None):
                return [0.0]

        embedder = DummyEmbedder()
        assert embedder.embed_batch([]) == []


class TestEchoProcessBatch:
    def test_echo_process_batch_shallow(self):
        """Shallow batch skips LLM entirely."""
        from engram.core.echo import EchoProcessor, EchoDepth

        class MockLLM:
            def generate(self, prompt):
                return '{"paraphrases": ["p1"], "keywords": ["k1"], "importance": 0.5}'

        processor = EchoProcessor(MockLLM())
        results = processor.process_batch(
            ["Hello world", "Goodbye world"],
            depth=EchoDepth.SHALLOW,
        )
        assert len(results) == 2
        for r in results:
            assert r.echo_depth == EchoDepth.SHALLOW

    def test_echo_process_batch_empty(self):
        from engram.core.echo import EchoProcessor

        class MockLLM:
            def generate(self, prompt):
                return "{}"

        processor = EchoProcessor(MockLLM())
        assert processor.process_batch([]) == []

    def test_echo_process_batch_single(self):
        """Single item goes through regular process()."""
        from engram.core.echo import EchoProcessor, EchoDepth

        class MockLLM:
            def generate(self, prompt):
                return '{"paraphrases": ["p1"], "keywords": ["k1"], "importance": 0.5}'

        processor = EchoProcessor(MockLLM())
        results = processor.process_batch(["Hello"], depth=EchoDepth.MEDIUM)
        assert len(results) == 1


class TestCategoryBatch:
    def test_detect_categories_batch_keyword_match(self):
        """Keyword matches should be resolved without LLM."""
        from engram.core.category import CategoryProcessor

        class MockLLM:
            def generate(self, prompt):
                return '{"action": "use_existing", "category_id": "facts", "confidence": 0.5}'

        class MockEmbedder:
            def embed(self, text, memory_action=None):
                return [0.0] * 10

        processor = CategoryProcessor(MockLLM(), MockEmbedder(), {"use_llm": False})

        # "preference" keyword should match preferences category
        results = processor.detect_categories_batch(
            ["I prefer dark mode", "Remember this fact"],
            use_llm=False,
        )
        assert len(results) == 2

    def test_detect_categories_batch_empty(self):
        from engram.core.category import CategoryProcessor

        class MockLLM:
            def generate(self, prompt):
                return "{}"

        processor = CategoryProcessor(MockLLM(), None)
        assert processor.detect_categories_batch([]) == []
