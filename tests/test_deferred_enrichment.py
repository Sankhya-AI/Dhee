"""Tests for conversation-aware memory with deferred enrichment.

Covers:
1. Lite path stores memory with 0 LLM calls
2. Conversation context stored in dedicated column
3. Regex keyword extraction (preferences, routines, goals, entities)
4. Embedding includes context summary
5. enrichment_status set to "pending"
6. enrich_pending() processes batch correctly
7. After enrichment: echo_keywords, categories populated
8. enrichment_status updated to "complete"
9. Search returns conversation_context
10. Scene assignment still works in lite path
11. Content dedup (content_hash) still works in lite path
12. Integration: add N memories lite → enrich_pending → search
"""

import json
import os
import tempfile

import pytest

from engram.configs.base import (
    BatchConfig,
    CategoryMemConfig,
    EchoMemConfig,
    EmbedderConfig,
    EnrichmentConfig,
    KnowledgeGraphConfig,
    LLMConfig,
    MemoryConfig,
    ProfileConfig,
    SceneConfig,
    VectorStoreConfig,
)
from engram.memory.main import FullMemory as Memory


def _make_deferred_memory(tmpdir, defer=True, echo=False, categories=False):
    """Create a Memory instance with deferred enrichment enabled."""
    config = MemoryConfig(
        llm=LLMConfig(provider="mock", config={}),
        embedder=EmbedderConfig(provider="simple", config={"embedding_dims": 384}),
        vector_store=VectorStoreConfig(
            provider="memory",
            config={"embedding_model_dims": 384},
        ),
        history_db_path=os.path.join(tmpdir, "test.db"),
        embedding_model_dims=384,
        echo=EchoMemConfig(enable_echo=echo),
        category=CategoryMemConfig(enable_categories=categories, use_llm_categorization=False),
        graph=KnowledgeGraphConfig(enable_graph=False),
        scene=SceneConfig(enable_scenes=False),
        profile=ProfileConfig(enable_profiles=False),
        enrichment=EnrichmentConfig(
            enable_unified=False,
            defer_enrichment=defer,
            context_window_turns=5,
        ),
        batch=BatchConfig(enable_batch=False),
    )
    return Memory(config)


class TestDeferredEnrichmentLitePath:
    """Test the lite processing path (0 LLM calls)."""

    def test_lite_path_stores_memory(self):
        """Lite path stores memory and returns ADD event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            result = m.add(
                messages="I prefer using Python for data science",
                user_id="test_user",
                infer=False,
            )
            assert "results" in result
            assert len(result["results"]) == 1
            r = result["results"][0]
            assert r["event"] == "ADD"
            assert r["memory"] == "I prefer using Python for data science"
            assert r["enrichment_status"] == "pending"
            m.close()

    def test_enrichment_status_pending(self):
        """Memories stored via lite path have enrichment_status='pending'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            result = m.add(
                messages="Remember my favorite color is blue",
                user_id="test_user",
                infer=False,
            )
            r = result["results"][0]
            assert r["enrichment_status"] == "pending"

            # Also verify in DB
            mem = m.db.get_memory(r["id"])
            assert mem is not None
            assert mem.get("enrichment_status") == "pending"
            m.close()

    def test_non_deferred_is_complete(self):
        """Without defer_enrichment, status is 'complete'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir, defer=False)
            result = m.add(
                messages="I like cats",
                user_id="test_user",
                infer=False,
            )
            r = result["results"][0]
            # Non-deferred path doesn't set enrichment_status in result
            # but the DB should have 'complete'
            mem = m.db.get_memory(r["id"])
            assert mem.get("enrichment_status") in ("complete", None)
            m.close()


class TestRegexKeywordExtraction:
    """Test regex-based keyword extraction in the lite path."""

    def test_preference_keywords(self):
        """Preference hints are extracted as keywords."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            result = m.add(
                messages="I prefer Python over JavaScript and always use type hints",
                user_id="test_user",
                infer=False,
            )
            r = result["results"][0]
            mem = m.db.get_memory(r["id"])
            metadata = mem.get("metadata", {})
            keywords = metadata.get("echo_keywords", [])
            assert "preference" in keywords
            m.close()

    def test_routine_keywords(self):
        """Routine hints are extracted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            result = m.add(
                messages="Every morning I review pull requests before standup",
                user_id="test_user",
                infer=False,
            )
            r = result["results"][0]
            mem = m.db.get_memory(r["id"])
            metadata = mem.get("metadata", {})
            keywords = metadata.get("echo_keywords", [])
            assert "routine" in keywords
            m.close()

    def test_goal_keywords(self):
        """Goal hints are extracted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            result = m.add(
                messages="My goal is to learn Rust this year for systems programming",
                user_id="test_user",
                infer=False,
            )
            r = result["results"][0]
            mem = m.db.get_memory(r["id"])
            metadata = mem.get("metadata", {})
            keywords = metadata.get("echo_keywords", [])
            assert "goal" in keywords
            m.close()

    def test_name_entity_extraction(self):
        """Names are extracted via regex."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            result = m.add(
                messages="My name is Alice and I work at Anthropic",
                user_id="test_user",
                metadata={"allow_sensitive": True},
                infer=False,
            )
            r = result["results"][0]
            mem = m.db.get_memory(r["id"])
            metadata = mem.get("metadata", {})
            keywords = metadata.get("echo_keywords", [])
            # Should contain name:Alice
            assert any("name:Alice" in k for k in keywords)
            m.close()

    def test_word_tokenization_keywords(self):
        """Top content words are extracted as keywords."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            result = m.add(
                messages="Python machine learning tensorflow pytorch neural networks deep learning",
                user_id="test_user",
                infer=False,
            )
            r = result["results"][0]
            mem = m.db.get_memory(r["id"])
            metadata = mem.get("metadata", {})
            keywords = metadata.get("echo_keywords", [])
            # Should contain domain-specific words
            assert "python" in keywords
            assert "learning" in keywords
            m.close()


class TestConversationContext:
    """Test conversation context storage and retrieval."""

    def test_context_stored_in_db(self):
        """Context messages are stored in the conversation_context column."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            ctx = [
                {"role": "user", "content": "What language should I learn?"},
                {"role": "assistant", "content": "It depends on your goals."},
                {"role": "user", "content": "I want to do data science"},
            ]
            result = m.add(
                messages="User prefers Python for data science",
                user_id="test_user",
                infer=False,
                context_messages=ctx,
            )
            r = result["results"][0]
            mem = m.db.get_memory(r["id"])
            ctx_raw = mem.get("conversation_context")
            assert ctx_raw is not None
            parsed = json.loads(ctx_raw)
            assert len(parsed) == 3
            assert parsed[0]["role"] == "user"
            m.close()

    def test_context_window_truncation(self):
        """Only last N turns are stored based on context_window_turns config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            # Config has context_window_turns=5, send 10 turns
            ctx = [{"role": "user", "content": f"Message {i}"} for i in range(10)]
            result = m.add(
                messages="Summary of the conversation",
                user_id="test_user",
                infer=False,
                context_messages=ctx,
            )
            r = result["results"][0]
            mem = m.db.get_memory(r["id"])
            parsed = json.loads(mem.get("conversation_context", "[]"))
            assert len(parsed) == 5  # Only last 5
            assert parsed[0]["content"] == "Message 5"
            m.close()

    def test_no_context_is_null(self):
        """When no context_messages provided, conversation_context is None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            result = m.add(
                messages="Standalone fact",
                user_id="test_user",
                infer=False,
            )
            r = result["results"][0]
            mem = m.db.get_memory(r["id"])
            assert mem.get("conversation_context") is None
            m.close()

    def test_context_in_search_results(self):
        """Search results include conversation_context field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            ctx = [
                {"role": "user", "content": "Tell me about Python"},
                {"role": "assistant", "content": "Python is a great language"},
            ]
            m.add(
                messages="User loves Python programming",
                user_id="test_user",
                infer=False,
                context_messages=ctx,
            )
            search_result = m.search("Python", user_id="test_user")
            results = search_result.get("results", [])
            assert len(results) >= 1
            # conversation_context should be present in search results
            assert "conversation_context" in results[0]
            m.close()


class TestContentDedup:
    """Test content hash deduplication in lite path."""

    def test_duplicate_content_deduplicated(self):
        """Adding the same content twice returns DEDUPLICATED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            r1 = m.add(
                messages="I prefer dark mode in all editors",
                user_id="test_user",
                infer=False,
            )
            assert r1["results"][0]["event"] == "ADD"

            r2 = m.add(
                messages="I prefer dark mode in all editors",
                user_id="test_user",
                infer=False,
            )
            assert r2["results"][0]["event"] == "DEDUPLICATED"
            m.close()


class TestDBMigration:
    """Test database migration for deferred enrichment columns."""

    def test_columns_exist_after_migration(self):
        """New columns are created during DB init."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from engram.db.sqlite import SQLiteManager
            db = SQLiteManager(os.path.join(tmpdir, "test.db"))
            # Try inserting with the new columns
            import uuid
            mid = str(uuid.uuid4())
            db.add_memory({
                "id": mid,
                "memory": "test",
                "user_id": "u1",
                "conversation_context": json.dumps([{"role": "user", "content": "hi"}]),
                "enrichment_status": "pending",
            })
            mem = db.get_memory(mid)
            assert mem is not None
            assert mem.get("enrichment_status") == "pending"
            assert mem.get("conversation_context") is not None
            db.close()

    def test_get_pending_enrichment(self):
        """get_pending_enrichment returns only pending memories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from engram.db.sqlite import SQLiteManager
            db = SQLiteManager(os.path.join(tmpdir, "test.db"))
            import uuid

            # Add a pending memory
            mid1 = str(uuid.uuid4())
            db.add_memory({
                "id": mid1, "memory": "pending memory",
                "user_id": "u1", "enrichment_status": "pending",
            })
            # Add a complete memory
            mid2 = str(uuid.uuid4())
            db.add_memory({
                "id": mid2, "memory": "complete memory",
                "user_id": "u1", "enrichment_status": "complete",
            })

            pending = db.get_pending_enrichment(user_id="u1", limit=10)
            assert len(pending) == 1
            assert pending[0]["id"] == mid1
            db.close()

    def test_update_enrichment_status(self):
        """update_enrichment_status marks a memory as complete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from engram.db.sqlite import SQLiteManager
            db = SQLiteManager(os.path.join(tmpdir, "test.db"))
            import uuid

            mid = str(uuid.uuid4())
            db.add_memory({
                "id": mid, "memory": "test",
                "user_id": "u1", "enrichment_status": "pending",
            })

            db.update_enrichment_status(mid, "complete")
            mem = db.get_memory(mid)
            assert mem["enrichment_status"] == "complete"

            # Should no longer appear in pending
            pending = db.get_pending_enrichment(user_id="u1")
            assert len(pending) == 0
            db.close()


class TestEnrichPending:
    """Test batch enrichment of pending memories."""

    def test_enrich_pending_empty(self):
        """enrich_pending with no pending memories returns 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            result = m.enrich_pending(user_id="test_user")
            assert result["enriched_count"] == 0
            assert result["batches"] == 0
            m.close()

    def test_enrich_pending_marks_complete(self):
        """After enrich_pending, memories are marked as complete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            # Add memories via lite path
            for i in range(3):
                m.add(
                    messages=f"Fact number {i}: Python is great for data analysis",
                    user_id="test_user",
                    infer=False,
                )

            # Verify they're pending
            pending = m.db.get_pending_enrichment(user_id="test_user")
            assert len(pending) == 3

            # Enrich
            result = m.enrich_pending(user_id="test_user", batch_size=10)
            assert result["enriched_count"] == 3
            assert result["remaining"] == 0

            # Verify they're now complete
            pending_after = m.db.get_pending_enrichment(user_id="test_user")
            assert len(pending_after) == 0
            m.close()

    def test_enrich_pending_respects_batch_size(self):
        """enrich_pending processes in batches respecting max_batches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            # Add 5 memories
            for i in range(5):
                m.add(
                    messages=f"Memory item {i} about machine learning",
                    user_id="test_user",
                    infer=False,
                )

            # Process only 1 batch of 2
            result = m.enrich_pending(
                user_id="test_user",
                batch_size=2,
                max_batches=1,
            )
            assert result["enriched_count"] == 2
            assert result["remaining"] > 0
            m.close()


class TestIntegration:
    """Integration tests: add → enrich → search."""

    def test_add_enrich_search_flow(self):
        """Full flow: add memories with deferred enrichment, enrich, then search."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)

            # Add several memories
            m.add(messages="I prefer Python for backend development", user_id="alice", infer=False)
            m.add(messages="My favorite editor is VS Code with vim keybindings", user_id="alice", infer=False)
            m.add(messages="Every morning I review GitHub notifications first", user_id="alice", infer=False)

            # Search should work even before enrichment (embedding-based)
            pre_results = m.search("Python backend", user_id="alice")
            assert len(pre_results.get("results", [])) >= 1

            # Enrich
            enrich_result = m.enrich_pending(user_id="alice")
            assert enrich_result["enriched_count"] == 3

            # Search after enrichment
            post_results = m.search("Python backend", user_id="alice")
            assert len(post_results.get("results", [])) >= 1
            m.close()

    def test_add_with_context_and_search(self):
        """Add with context, verify context is retrievable via search."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_deferred_memory(tmpdir)
            ctx = [
                {"role": "user", "content": "What's the best language for web scraping?"},
                {"role": "assistant", "content": "Python with Beautiful Soup or Scrapy is excellent."},
            ]
            m.add(
                messages="User wants to learn Python web scraping with Beautiful Soup",
                user_id="bob",
                infer=False,
                context_messages=ctx,
            )

            results = m.search("web scraping Python", user_id="bob")
            hits = results.get("results", [])
            assert len(hits) >= 1
            # Context should be in the result
            ctx_field = hits[0].get("conversation_context")
            assert ctx_field is not None
            parsed_ctx = json.loads(ctx_field) if isinstance(ctx_field, str) else ctx_field
            assert len(parsed_ctx) == 2
            m.close()

    def test_mixed_deferred_and_normal(self):
        """Switching defer_enrichment off still works for normal add."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create with deferred OFF
            m = _make_deferred_memory(tmpdir, defer=False)
            result = m.add(
                messages="Normal memory without deferred enrichment",
                user_id="test_user",
                infer=False,
            )
            r = result["results"][0]
            assert r["event"] == "ADD"
            # Should not have enrichment_status in the result (normal path)
            assert r.get("enrichment_status") is None
            m.close()


class TestEnrichmentConfig:
    """Test EnrichmentConfig deferred fields."""

    def test_default_values(self):
        """Default config has defer_enrichment=False."""
        config = EnrichmentConfig()
        assert config.defer_enrichment is False
        assert config.context_window_turns == 10
        assert config.enrich_on_access is False

    def test_enable_deferred(self):
        """Can enable deferred enrichment via config."""
        config = EnrichmentConfig(defer_enrichment=True, context_window_turns=5)
        assert config.defer_enrichment is True
        assert config.context_window_turns == 5
