"""Tests for MemoryConfig preset factory methods."""

from dhee.configs.base import MemoryConfig


class TestMemoryPresets:
    def test_minimal_no_llm(self):
        c = MemoryConfig.minimal()
        assert c.llm.provider == "mock"
        assert c.embedder.provider == "simple"

    def test_minimal_disables_features(self):
        c = MemoryConfig.minimal()
        assert c.echo.enable_echo is False
        assert c.category.enable_categories is False
        assert c.graph.enable_graph is False
        assert c.scene.enable_scenes is False
        assert c.profile.enable_profiles is False

    def test_smart_detects_provider(self):
        c = MemoryConfig.smart()
        # Smart should use the best available provider
        assert c.embedder.provider in {"gemini", "openai", "ollama", "simple"}
        assert c.llm.provider in {"gemini", "openai", "ollama", "mock"}

    def test_smart_no_scenes(self):
        c = MemoryConfig.smart()
        assert c.scene.enable_scenes is False
        assert c.profile.enable_profiles is False

    def test_full_has_scenes(self):
        c = MemoryConfig.full()
        assert c.scene.enable_scenes is True
        assert c.profile.enable_profiles is True

    def test_full_has_echo(self):
        c = MemoryConfig.full()
        assert c.echo.enable_echo is True
        assert c.category.enable_categories is True
        assert c.graph.enable_graph is True

    def test_minimal_uses_memory_vector_store(self):
        c = MemoryConfig.minimal()
        assert c.vector_store.provider == "memory"

    def test_minimal_dims_384(self):
        c = MemoryConfig.minimal()
        assert c.embedding_model_dims == 384
