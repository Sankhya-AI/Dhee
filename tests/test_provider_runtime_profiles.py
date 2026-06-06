from dhee.provider_defaults import (
    embedding_dims_for,
    provider_reranker_config,
    provider_runtime_profile,
)


def test_nvidia_profile_keeps_single_key_for_embedding_and_rerank():
    profile = provider_runtime_profile("nvidia", api_key="nvapi-test")

    assert profile["provider"] == "nvidia"
    assert profile["embedder_model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert profile["embedding_dims"] == 2048
    assert profile["reranker_config"]["model"] == "nvidia/llama-nemotron-rerank-vl-1b-v2"
    assert profile["single_api_key_contract"]["env_var"] == "NVIDIA_API_KEY"
    assert profile["single_api_key_contract"]["reranker_same_key"] is True
    assert "dhee_reranker" in profile["single_api_key_contract"]["applies_to"]


def test_official_provider_profiles_use_fact_checked_defaults():
    openai = provider_runtime_profile("openai", llm_api_key="sk-test")
    gemini = provider_runtime_profile("gemini", llm_api_key="google-test")
    ollama = provider_runtime_profile("ollama")

    assert openai["llm_model"] == "gpt-5.2"
    assert openai["fast_llm_model"] == "gpt-5-mini"
    assert openai["coding_llm_model"] == "gpt-5.2-codex"
    assert openai["llm_provider"] == "openai"
    assert openai["embedder_provider"] == "nvidia"
    assert openai["reranker_provider"] == "nvidia"
    assert openai["embedder_model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert openai["embedding_dims"] == 2048
    assert openai["reranker_config"]["model"] == "nvidia/llama-nemotron-rerank-vl-1b-v2"
    assert openai["single_api_key_contract"]["env_var"] == "OPENAI_API_KEY"
    assert openai["memory_key_contract"]["embedder_env_var"] == "NVIDIA_API_KEY"
    assert openai["memory_key_contract"]["split_key_required"] is True
    assert openai["llm_config"]["api_key"] == "sk-test"
    assert "api_key" not in openai["embedder_config"]
    assert "api_key" not in openai["reranker_config"]

    assert gemini["llm_model"] == "gemini-2.5-pro"
    assert gemini["fast_llm_model"] == "gemini-2.5-flash"
    assert gemini["llm_provider"] == "gemini"
    assert gemini["embedder_provider"] == "nvidia"
    assert gemini["reranker_provider"] == "nvidia"
    assert gemini["embedder_model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert gemini["embedding_dims"] == 2048
    assert gemini["single_api_key_contract"]["env_var"] == "GOOGLE_API_KEY"

    assert ollama["llm_model"] == "qwen3.6"
    assert ollama["fast_llm_model"] == "llama3.2"
    assert ollama["llm_provider"] == "ollama"
    assert ollama["embedder_provider"] == "nvidia"
    assert ollama["embedder_model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert ollama["embedding_dims"] == 2048
    assert ollama["single_api_key_contract"]["env_var"] is None


def test_anthropic_profile_uses_nvidia_memory_by_default():
    profile = provider_runtime_profile("anthropic", llm_api_key="sk-ant-test")

    assert profile["llm_model"] == "claude-opus-4-7"
    assert profile["fast_llm_model"] == "claude-sonnet-4-6"
    assert profile["small_llm_model"] == "claude-haiku-4-5-20251001"
    assert profile["llm_provider"] == "anthropic"
    assert profile["embedder_provider"] == "nvidia"
    assert profile["reranker_provider"] == "nvidia"
    assert profile["embedder_model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert profile["embedder_config"]["model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert profile["reranker_config"]["model"] == "nvidia/llama-nemotron-rerank-vl-1b-v2"
    assert profile["single_api_key_contract"]["env_var"] == "ANTHROPIC_API_KEY"
    assert profile["single_api_key_contract"]["applies_to"] == ["chotu", "dhee_llm", "dhee_embedder", "dhee_reranker"]
    assert profile["memory_key_contract"]["embedder_env_var"] == "NVIDIA_API_KEY"
    assert profile["llm_config"]["api_key"] == "sk-ant-test"
    assert "api_key" not in profile["embedder_config"]
    assert "api_key" not in profile["reranker_config"]


def test_split_profile_routes_memory_keys_independently():
    profile = provider_runtime_profile(
        "openai",
        llm_api_key="openai-key",
        embedder_api_key="nvidia-memory-key",
        reranker_api_key="nvidia-rerank-key",
    )

    assert profile["llm_config"]["api_key"] == "openai-key"
    assert profile["embedder_config"]["api_key"] == "nvidia-memory-key"
    assert profile["reranker_config"]["api_key"] == "nvidia-rerank-key"
    assert profile["key_routing"]["embedder_uses_legacy_api_key"] is False
    assert profile["key_routing"]["reranker_uses_legacy_api_key"] is False


def test_profile_allows_explicit_non_nvidia_memory_override():
    profile = provider_runtime_profile(
        "anthropic",
        embedder_provider="ollama",
        reranker_provider="ollama",
    )

    assert profile["llm_provider"] == "anthropic"
    assert profile["embedder_provider"] == "ollama"
    assert profile["reranker_provider"] == "ollama"
    assert profile["embedder_model"] == "qwen3-embedding"
    assert profile["embedding_dims"] == 4096
    assert profile["reranker_config"] is None
    assert profile["memory_key_contract"]["default_memory_provider"] == "nvidia"


def test_embedding_dimension_aliases_cover_hackathon_models():
    assert embedding_dims_for("openai", "text-embedding-3-large") == 3072
    assert embedding_dims_for("gemini", "gemini-embedding-2") == 3072
    assert embedding_dims_for("ollama", "qwen3-embedding") == 4096
    assert provider_reranker_config("openai") is None
