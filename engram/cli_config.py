"""Config manager for ~/.engram/config.json."""

import json
import os
from typing import Any, Dict, Optional

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".engram")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Provider → default models and env var
PROVIDER_DEFAULTS = {
    "gemini": {
        "llm_model": "gemini-2.0-flash",
        "embedder_model": "gemini-embedding-001",
        "embedding_dims": 3072,
        "env_var": "GOOGLE_API_KEY",
        "alt_env_vars": ["GEMINI_API_KEY"],
    },
    "openai": {
        "llm_model": "gpt-4o-mini",
        "embedder_model": "text-embedding-3-small",
        "embedding_dims": 1536,
        "env_var": "OPENAI_API_KEY",
        "alt_env_vars": [],
    },
    "nvidia": {
        "llm_model": "moonshotai/kimi-k2.5",
        "embedder_model": "nvidia/nv-embedqa-e5-v5",
        "embedding_dims": 1024,
        "env_var": "NVIDIA_API_KEY",
        "alt_env_vars": [],
    },
    "ollama": {
        "llm_model": "llama3.1",
        "embedder_model": "nomic-embed-text",
        "embedding_dims": 768,
        "env_var": None,
        "alt_env_vars": [],
    },
}


def get_default_config() -> Dict[str, Any]:
    """Return default config structure."""
    return {
        "version": "1",
        "provider": "gemini",
        "packages": ["engram-memory"],
    }


def load_config() -> Dict[str, Any]:
    """Load config from ~/.engram/config.json or return defaults."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return get_default_config()


def save_config(config: Dict[str, Any]) -> None:
    """Write config to ~/.engram/config.json."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def get_api_key(provider: str) -> Optional[str]:
    """Read the API key from environment for the given provider."""
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    env_var = defaults.get("env_var")
    if env_var:
        key = os.environ.get(env_var)
        if key:
            return key
    for alt in defaults.get("alt_env_vars", []):
        key = os.environ.get(alt)
        if key:
            return key
    return None


def get_memory_instance(config: Optional[Dict[str, Any]] = None):
    """Build a Memory instance from CLI config."""
    from engram.memory.main import Memory
    from engram.configs.base import (
        MemoryConfig,
        VectorStoreConfig,
        LLMConfig,
        EmbedderConfig,
        FadeMemConfig,
    )

    if config is None:
        config = load_config()

    provider = config.get("provider", "gemini")
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["gemini"])

    api_key = get_api_key(provider)
    if not api_key and provider != "ollama":
        env_var = defaults["env_var"]
        raise RuntimeError(
            f"No API key found. Set {env_var} environment variable.\n"
            f"  export {env_var}=your-key-here"
        )

    llm_model = config.get("llm_model", defaults["llm_model"])
    embedder_model = config.get("embedder_model", defaults["embedder_model"])
    embedding_dims = config.get("embedding_dims", defaults["embedding_dims"])

    llm_cfg = {"model": llm_model, "temperature": 0.1, "max_tokens": 1024}
    embedder_cfg = {"model": embedder_model}
    if api_key:
        llm_cfg["api_key"] = api_key
        embedder_cfg["api_key"] = api_key

    vec_db_path = os.path.join(CONFIG_DIR, "sqlite_vec.db")
    history_db_path = os.path.join(CONFIG_DIR, "history.db")

    memory_config = MemoryConfig(
        vector_store=VectorStoreConfig(
            provider="sqlite_vec",
            config={
                "path": vec_db_path,
                "collection_name": "fadem_memories",
                "embedding_model_dims": embedding_dims,
            },
        ),
        llm=LLMConfig(provider=provider, config=llm_cfg),
        embedder=EmbedderConfig(provider=provider, config=embedder_cfg),
        history_db_path=history_db_path,
        embedding_model_dims=embedding_dims,
        engram=FadeMemConfig(enable_forgetting=True),
    )

    return Memory(memory_config)
