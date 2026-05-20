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
        "llm_model": "gemini-2.0-flash",
        "embedder_model": "gemini-embedding-001",
        "embedding_dims": 3072,
        "env_var": "GOOGLE_API_KEY",
        "alt_env_vars": ["GEMINI_API_KEY", "DHEE_GEMINI_API_KEY", "FADEM_GEMINI_API_KEY"],
    },
    "openai": {
        "llm_model": "gpt-4o-mini",
        "embedder_model": "text-embedding-3-small",
        "embedding_dims": 1536,
        "env_var": "OPENAI_API_KEY",
        "alt_env_vars": ["DHEE_OPENAI_API_KEY", "FADEM_OPENAI_API_KEY"],
    },
    "ollama": {
        "llm_model": "llama3.1",
        "embedder_model": "nomic-embed-text",
        "embedding_dims": 768,
        "env_var": None,
        "alt_env_vars": [],
    },
    "mock": {
        "llm_model": "mock",
        "embedder_model": "simple",
        "embedding_dims": 384,
        "env_var": None,
        "alt_env_vars": [],
    },
    "simple": {
        "llm_model": "mock",
        "embedder_model": "simple",
        "embedding_dims": 384,
        "env_var": None,
        "alt_env_vars": [],
    },
    "qwen": {
        "llm_model": "dhee",
        "embedder_model": "Qwen/Qwen3-Embedding-0.6B",
        "embedding_dims": 1024,
        "env_var": None,
        "alt_env_vars": [],
    },
}

EMBEDDING_DIMS_BY_MODEL: Dict[str, int] = {
    "models/text-embedding-005": 768,
    "text-embedding-005": 768,
    "gemini-embedding-001": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "nvidia/llama-nemotron-embed-vl-1b-v2": 2048,
    "nvidia/llama-nemotron-embed-1b-v2": 2048,
    "nvidia/llama-3.2-nv-embedqa-1b-v2": 2048,
    "nvidia/nv-embed-v1": 4096,
    "nvidia/nv-embedqa-e5-v5": 1024,
}


def provider_defaults(provider: Optional[str] = None) -> Dict[str, Any]:
    key = str(provider or DEFAULT_PROVIDER).strip().lower()
    return dict(PROVIDER_DEFAULTS.get(key, PROVIDER_DEFAULTS[DEFAULT_PROVIDER]))


def embedding_dims_for(provider: Optional[str] = None, model: Optional[str] = None) -> int:
    if model and model in EMBEDDING_DIMS_BY_MODEL:
        return EMBEDDING_DIMS_BY_MODEL[model]
    return int(provider_defaults(provider).get("embedding_dims", DEFAULT_NVIDIA_EMBEDDING_DIMS))
