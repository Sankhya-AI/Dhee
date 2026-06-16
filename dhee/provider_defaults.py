"""Provider defaults shared across Dhee runtime surfaces."""

from __future__ import annotations

from typing import Any, Dict, Optional


DEFAULT_PROVIDER = "nvidia"
DEFAULT_COLLECTION = "dhee"

DEFAULT_NVIDIA_LLM_MODEL = "moonshotai/kimi-k2.5"
DEFAULT_NVIDIA_EMBEDDER_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2"
DEFAULT_NVIDIA_RERANK_MODEL = "nvidia/llama-nemotron-rerank-vl-1b-v2"
DEFAULT_NVIDIA_EMBEDDING_DIMS = 2048

PROVIDER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "nvidia": {
        "llm_model": DEFAULT_NVIDIA_LLM_MODEL,
        "embedder_model": DEFAULT_NVIDIA_EMBEDDER_MODEL,
        "embedding_dims": DEFAULT_NVIDIA_EMBEDDING_DIMS,
        "reranker_provider": "nvidia",
        "reranker_model": DEFAULT_NVIDIA_RERANK_MODEL,
        "reranker_api_key_env": "NVIDIA_API_KEY",
        "env_var": "NVIDIA_API_KEY",
        "alt_env_vars": [
            "NVIDIA_QWEN_API_KEY",
            "NVIDIA_EMBEDDING_API_KEY",
            "NVIDIA_EMBED_API_KEY",
            "NVIDIA_LLAMA_4_MAV_API_KEY",
            "DHEE_NVIDIA_API_KEY",
            "FADEM_NVIDIA_API_KEY",
        ],
    },
    "gemini": {
        "llm_model": "gemini-2.5-pro",
        "fast_llm_model": "gemini-2.5-flash",
        "embedder_model": "gemini-embedding-2",
        "embedding_dims": 3072,
        "reranker_provider": None,
        "reranker_model": "",
        "reranker_api_key_env": None,
        "env_var": "GOOGLE_API_KEY",
        "alt_env_vars": ["GEMINI_API_KEY", "DHEE_GEMINI_API_KEY", "FADEM_GEMINI_API_KEY"],
    },
    "openai": {
        "llm_model": "gpt-5.2",
        "fast_llm_model": "gpt-5-mini",
        "coding_llm_model": "gpt-5.2-codex",
        "embedder_model": "text-embedding-3-large",
        "embedding_dims": 3072,
        "reranker_provider": None,
        "reranker_model": "",
        "reranker_api_key_env": None,
        "env_var": "OPENAI_API_KEY",
        "alt_env_vars": ["DHEE_OPENAI_API_KEY", "FADEM_OPENAI_API_KEY"],
    },
    "anthropic": {
        "llm_model": "claude-opus-4-7",
        "fast_llm_model": "claude-sonnet-4-6",
        "small_llm_model": "claude-haiku-4-5-20251001",
        "embedder_model": None,
        "embedding_dims": 384,
        "reranker_provider": None,
        "reranker_model": "",
        "reranker_api_key_env": None,
        "env_var": "ANTHROPIC_API_KEY",
        "alt_env_vars": ["CLAUDE_API_KEY", "DHEE_ANTHROPIC_API_KEY", "FADEM_ANTHROPIC_API_KEY"],
    },
    "ollama": {
        "llm_model": "qwen3.6",
        "fast_llm_model": "llama3.2",
        "embedder_model": "qwen3-embedding",
        "embedding_dims": 4096,
        "reranker_provider": None,
        "reranker_model": "",
        "reranker_api_key_env": None,
        "env_var": None,
        "alt_env_vars": [],
    },
    "mock": {
        "llm_model": "mock",
        "embedder_model": "simple",
        "embedding_dims": 384,
        "reranker_provider": None,
        "reranker_model": "",
        "reranker_api_key_env": None,
        "env_var": None,
        "alt_env_vars": [],
    },
    "simple": {
        "llm_model": "mock",
        "embedder_model": "simple",
        "embedding_dims": 384,
        "reranker_provider": None,
        "reranker_model": "",
        "reranker_api_key_env": None,
        "env_var": None,
        "alt_env_vars": [],
    },
    "qwen": {
        "llm_model": "dhee",
        "embedder_model": "Qwen/Qwen3-Embedding-0.6B",
        "embedding_dims": 1024,
        "reranker_provider": "qwen",
        "reranker_model": "Qwen/Qwen3-Reranker-0.6B",
        "reranker_api_key_env": None,
        "env_var": None,
        "alt_env_vars": [],
    },
}

EMBEDDING_DIMS_BY_MODEL: Dict[str, int] = {
    "models/text-embedding-005": 768,
    "text-embedding-005": 768,
    "gemini-embedding-001": 3072,
    "gemini-embedding-2": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "qwen3-embedding": 4096,
    "qwen3-embedding:latest": 4096,
    "qwen3-embedding:8b": 4096,
    "qwen3-embedding:4b": 2560,
    "qwen3-embedding:0.6b": 1024,
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "nvidia/llama-nemotron-embed-vl-1b-v2": 2048,
    "nvidia/llama-nemotron-embed-1b-v2": 2048,
    "nvidia/llama-3.2-nv-embedqa-1b-v2": 2048,
    "nvidia/nv-embed-v1": 4096,
    "nvidia/nv-embedqa-e5-v5": 1024,
}


def normalize_provider(provider: Optional[str] = None) -> str:
    key = str(provider or DEFAULT_PROVIDER).strip().lower().replace("-", "_")
    aliases = {
        "claude": "anthropic",
        "google": "gemini",
        "google_gemini": "gemini",
        "nvidia_nim": "nvidia",
        "nim": "nvidia",
        "local": "ollama",
    }
    return aliases.get(key, key)


def provider_defaults(provider: Optional[str] = None) -> Dict[str, Any]:
    key = normalize_provider(provider)
    return dict(PROVIDER_DEFAULTS.get(key, PROVIDER_DEFAULTS[DEFAULT_PROVIDER]))


def embedding_dims_for(provider: Optional[str] = None, model: Optional[str] = None) -> int:
    if model:
        exact = EMBEDDING_DIMS_BY_MODEL.get(model)
        if exact:
            return int(exact)
        lowered = EMBEDDING_DIMS_BY_MODEL.get(str(model).strip().lower())
        if lowered:
            return int(lowered)
    return int(provider_defaults(provider).get("embedding_dims", DEFAULT_NVIDIA_EMBEDDING_DIMS))


def provider_llm_config(
    provider: Optional[str] = None,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    host: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Build an LLM config for a provider without assuming NVIDIA."""

    provider_id = normalize_provider(provider)
    defaults = provider_defaults(provider_id)
    config: Dict[str, Any] = {
        "model": model or defaults.get("llm_model"),
    }
    if provider_id == "nvidia":
        config["temperature"] = 0.2 if temperature is None else temperature
        config["max_tokens"] = 4096 if max_tokens is None else max_tokens
    elif provider_id in {"openai", "gemini", "ollama"}:
        config["temperature"] = 0.1 if temperature is None else temperature
        config["max_tokens"] = 1024 if max_tokens is None else max_tokens
    if api_key:
        config["api_key"] = api_key
    if base_url:
        config["base_url"] = base_url
    if host:
        config["host"] = host
    return {key: value for key, value in config.items() if value not in {None, ""}}


def provider_embedder_config(
    provider: Optional[str] = None,
    *,
    model: Optional[str] = None,
    embedding_dims: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an embedder config with model-correct dimensions."""

    provider_id = normalize_provider(provider)
    defaults = provider_defaults(provider_id)
    embedder_model = model or defaults.get("embedder_model")
    dims = int(embedding_dims or embedding_dims_for(provider_id, str(embedder_model or "")))
    if provider_id == "anthropic":
        return {}
    if provider_id in {"simple", "mock"}:
        return {"embedding_dims": dims}
    config: Dict[str, Any] = {
        "model": embedder_model,
        "embedding_dims": dims,
    }
    if api_key:
        config["api_key"] = api_key
    if base_url:
        config["base_url"] = base_url
    if host:
        config["host"] = host
    return {key: value for key, value in config.items() if value not in {None, ""}}


def provider_reranker_config(
    provider: Optional[str] = None,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    strict_schema: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return a reranker config for providers that have a known reranker.

    Hosted OpenAI/Gemini/Ollama profiles intentionally return None today:
    they have first-class embedding profiles, but no same-key reranker backend
    in Dhee's runtime. This keeps model agnosticism honest instead of silently
    falling back to NVIDIA.
    """

    defaults = provider_defaults(provider)
    reranker_provider = defaults.get("reranker_provider")
    reranker_model = model or defaults.get("reranker_model")
    if not reranker_provider or not reranker_model:
        return None
    config: Dict[str, Any] = {
        "provider": reranker_provider,
        "model": reranker_model,
        "strict_schema": strict_schema,
    }
    api_key_env = defaults.get("reranker_api_key_env")
    if api_key_env:
        config["api_key_env"] = api_key_env
    if api_key:
        config["api_key"] = api_key
    return config


def provider_runtime_profile(
    provider: Optional[str] = None,
    *,
    llm_provider: Optional[str] = None,
    embedder_provider: Optional[str] = None,
    reranker_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    embedder_model: Optional[str] = None,
    reranker_model: Optional[str] = None,
    embedding_dims: Optional[int] = None,
    api_key: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    embedder_api_key: Optional[str] = None,
    reranker_api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a Dhee runtime profile with independent LLM/memory lanes.

    ``provider`` is a convenience default for all lanes. Production agents can
    set a large Chotu/agent LLM provider separately while keeping Dhee's
    embedder and reranker on NVIDIA, which is the default memory provider.
    """

    provider_id = normalize_provider(provider)
    llm_provider_id = normalize_provider(llm_provider or provider_id)
    embedder_provider_id = normalize_provider(embedder_provider or DEFAULT_PROVIDER)
    reranker_provider_id = normalize_provider(reranker_provider or embedder_provider_id)
    llm_defaults = provider_defaults(llm_provider_id)
    embedder_defaults = provider_defaults(embedder_provider_id)
    reranker_defaults = provider_defaults(reranker_provider_id)
    selected_embedder_model = embedder_model or embedder_defaults.get("embedder_model")
    dims = int(embedding_dims or embedding_dims_for(embedder_provider_id, str(selected_embedder_model or "")))
    applies_to = ["dhee_llm"]
    if llm_provider_id == provider_id:
        applies_to.insert(0, "chotu")
    if selected_embedder_model:
        applies_to.append("dhee_embedder")
    if reranker_defaults.get("reranker_provider") == reranker_provider_id:
        applies_to.append("dhee_reranker")

    resolved_llm_api_key = llm_api_key or api_key
    resolved_embedder_api_key = embedder_api_key
    if resolved_embedder_api_key is None and embedder_provider_id == llm_provider_id:
        resolved_embedder_api_key = api_key
    resolved_reranker_api_key = reranker_api_key
    if resolved_reranker_api_key is None and reranker_provider_id == llm_provider_id:
        resolved_reranker_api_key = api_key
    if resolved_reranker_api_key is None and reranker_provider_id == embedder_provider_id:
        resolved_reranker_api_key = resolved_embedder_api_key

    return {
        "provider": provider_id,
        "llm_provider": llm_provider_id,
        "embedder_provider": embedder_provider_id,
        "reranker_provider": reranker_provider_id,
        "llm_model": llm_model or llm_defaults.get("llm_model"),
        "fast_llm_model": llm_defaults.get("fast_llm_model"),
        "small_llm_model": llm_defaults.get("small_llm_model"),
        "coding_llm_model": llm_defaults.get("coding_llm_model"),
        "embedder_model": selected_embedder_model,
        "embedding_dims": dims,
        "single_api_key_contract": {
            "env_var": llm_defaults.get("env_var"),
            "alt_env_vars": list(llm_defaults.get("alt_env_vars") or []),
            "applies_to": applies_to,
            "reranker_same_key": reranker_defaults.get("reranker_provider") == llm_provider_id,
            "notes": (
                "Provider has no first-party same-key Dhee embedding/reranker profile; "
                "Dhee must use local/simple embeddings or an explicitly selected "
                "embedding provider, never a silent fallback."
                if llm_provider_id == "anthropic" and embedder_provider_id == "anthropic"
                else "Provider has no first-party same-key Dhee reranker profile; "
                "Dhee must not silently fall back to another provider key."
                if not reranker_defaults.get("reranker_provider")
                else "The same provider key is valid for LLM, embedding, and reranking."
            ),
        },
        "env_var": llm_defaults.get("env_var"),
        "alt_env_vars": list(llm_defaults.get("alt_env_vars") or []),
        "memory_key_contract": {
            "embedder_env_var": embedder_defaults.get("env_var"),
            "embedder_alt_env_vars": list(embedder_defaults.get("alt_env_vars") or []),
            "reranker_env_var": reranker_defaults.get("env_var"),
            "reranker_alt_env_vars": list(reranker_defaults.get("alt_env_vars") or []),
            "default_memory_provider": DEFAULT_PROVIDER,
            "split_key_required": embedder_provider_id != llm_provider_id or reranker_provider_id != llm_provider_id,
        },
        "key_routing": {
            "llm_uses_legacy_api_key": bool(api_key and not llm_api_key),
            "embedder_uses_legacy_api_key": bool(api_key and resolved_embedder_api_key == api_key),
            "reranker_uses_legacy_api_key": bool(api_key and resolved_reranker_api_key == api_key),
        },
        "llm_config": provider_llm_config(
            llm_provider_id,
            model=llm_model,
            api_key=resolved_llm_api_key,
            base_url=base_url,
            host=host,
        ),
        "embedder_config": provider_embedder_config(
            embedder_provider_id,
            model=embedder_model,
            embedding_dims=dims,
            api_key=resolved_embedder_api_key,
            base_url=base_url,
            host=host,
        ),
        "reranker_config": provider_reranker_config(
            reranker_provider_id,
            model=reranker_model,
            api_key=resolved_reranker_api_key,
        ),
    }
