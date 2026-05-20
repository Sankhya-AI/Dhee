"""Config manager for ~/.dhee/config.json."""

import json
import os
import sqlite3
from typing import Any, Dict, Optional

from dhee.configs.base import VectorStoreConfig
from dhee.configs.base import _dhee_data_dir
from dhee.provider_defaults import (
    DEFAULT_COLLECTION,
    DEFAULT_PROVIDER,
    PROVIDER_DEFAULTS,
    provider_defaults,
)


class _DynamicPath:
    def __init__(self, resolver):
        self._resolver = resolver

    def __fspath__(self) -> str:
        return str(self._resolver())

    def __str__(self) -> str:
        return self.__fspath__()

    def __repr__(self) -> str:
        return repr(self.__fspath__())


def get_config_dir() -> str:
    """Return Dhee's current config/data directory.

    Keep this dynamic so tests, embedded runtimes, and harness installers that
    set ``HOME`` or ``DHEE_DATA_DIR`` after import do not accidentally write to
    the real user profile.
    """
    return _dhee_data_dir()


def get_config_path() -> str:
    return os.path.join(get_config_dir(), "config.json")


CONFIG_DIR = _DynamicPath(get_config_dir)
CONFIG_PATH = _DynamicPath(get_config_path)

def get_default_config() -> Dict[str, Any]:
    """Return default config structure."""
    defaults = provider_defaults(DEFAULT_PROVIDER)
    return {
        "version": "1",
        "provider": DEFAULT_PROVIDER,
        "llm_model": defaults["llm_model"],
        "embedder_model": defaults["embedder_model"],
        "embedding_dims": defaults["embedding_dims"],
        "vector_store": {
            "provider": "zvec",
            "config": {
                "collection_name": DEFAULT_COLLECTION,
                "embedding_model_dims": defaults["embedding_dims"],
            },
        },
        "packages": ["engram-memory"],
        "identity": {
            "user_id": "default",
        },
        "harnesses": {
            "claude_code": {
                "enabled": True,
                "router": True,
                "shared_task_context": True,
            },
            "codex": {
                "enabled": True,
                "shared_task_context": True,
                "auto_sync": True,
            },
        },
    }


def _merge_defaults(config: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(defaults)
    for key, value in config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(value, merged[key])  # type: ignore[arg-type]
        else:
            merged[key] = value
    return merged


def load_config() -> Dict[str, Any]:
    """Load config from ~/.dhee/config.json or return defaults."""
    defaults = get_default_config()
    config_path = get_config_path()
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return _merge_defaults(json.load(f), defaults)
    return defaults


def save_config(config: Dict[str, Any]) -> None:
    """Write config to ~/.dhee/config.json."""
    config_dir = get_config_dir()
    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "config.json"), "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def get_api_key(provider: str) -> Optional[str]:
    """Read the API key for the given provider.

    Environment variables win for backward compatibility. If none are
    set, fall back to Dhee's encrypted local secret store.
    """
    defaults = provider_defaults(provider)
    env_var = defaults.get("env_var")
    if env_var:
        key = os.environ.get(env_var)
        if key:
            return key
    for alt in defaults.get("alt_env_vars", []):
        key = os.environ.get(alt)
        if key:
            return key
    try:
        from dhee.secret_store import get_api_key as get_secret_api_key

        key, _source, _env_var = get_secret_api_key(provider)
        return key
    except Exception:
        return None


def _history_embedding_dims(config_dir: str) -> Optional[int]:
    """Return the dominant stored embedding dimension from history.db."""
    history_path = os.path.join(config_dir, "history.db")
    if not os.path.exists(history_path):
        return None
    try:
        conn = sqlite3.connect(history_path)
        try:
            rows = conn.execute(
                """
                SELECT embedding
                FROM memories
                WHERE tombstone = 0
                  AND embedding IS NOT NULL
                  AND embedding != ''
                LIMIT 200
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    counts: Dict[int, int] = {}
    for (raw_embedding,) in rows:
        try:
            parsed = json.loads(raw_embedding) if isinstance(raw_embedding, str) else raw_embedding
        except Exception:
            continue
        if isinstance(parsed, list) and parsed:
            counts[len(parsed)] = counts.get(len(parsed), 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _resolve_vector_store_config(
    config: Dict[str, Any],
    config_dir: str,
    embedding_dims: int,
) -> VectorStoreConfig:
    """Resolve the persistent vector store from config, defaulting to zvec."""
    configured_vector = config.get("vector_store")
    if isinstance(configured_vector, dict):
        provider = str(configured_vector.get("provider") or "").strip()
        vector_config = dict(configured_vector.get("config") or {})
    else:
        provider = str(
            config.get("vector_store_provider")
            or config.get("vector_provider")
            or ""
        ).strip()
        vector_config = dict(config.get("vector_store_config") or {})

    provider = provider.lower()
    if not provider:
        provider = "zvec"

    collection_name = str(
        vector_config.get("collection_name")
        or config.get("collection_name")
        or DEFAULT_COLLECTION
    )
    vector_config["collection_name"] = collection_name
    vector_config["embedding_model_dims"] = int(
        vector_config.get("embedding_model_dims")
        or vector_config.get("vector_size")
        or vector_config.get("embedding_dims")
        or embedding_dims
    )

    if provider == "zvec":
        vector_config["path"] = str(vector_config.get("path") or os.path.join(config_dir, "zvec"))
    elif provider == "sqlite_vec":
        vector_config["path"] = str(
            vector_config.get("path") or os.path.join(config_dir, "sqlite_vec.db")
        )
    else:
        raise RuntimeError(f"Unsupported persistent vector store provider: {provider}")

    return VectorStoreConfig(provider=provider, config=vector_config)


def get_memory_instance(config: Optional[Dict[str, Any]] = None):
    """Build a Memory instance from CLI config."""
    from dhee.memory.main import FullMemory
    from dhee.configs.base import (
        MemoryConfig,
        VectorStoreConfig,
        LLMConfig,
        EmbedderConfig,
        FadeMemConfig,
    )

    if config is None:
        config = load_config()

    provider = config.get("provider", DEFAULT_PROVIDER)
    defaults = provider_defaults(provider)
    config_dir = get_config_dir()
    existing_embedding_dims = _history_embedding_dims(config_dir)
    preserve_existing_dims = bool(config.get("preserve_existing_embedding_dims", False))

    api_key = get_api_key(provider)
    if not api_key and provider != "ollama":
        if existing_embedding_dims:
            runtime_provider = "mock"
        else:
            env_var = defaults["env_var"]
            raise RuntimeError(
                f"No API key found. Set {env_var} environment variable.\n"
                f"  export {env_var}=your-key-here"
            )
    else:
        runtime_provider = provider

    llm_model = config.get("llm_model", defaults["llm_model"])
    embedder_model = config.get("embedder_model", defaults["embedder_model"])
    configured_embedding_dims = int(config.get("embedding_dims", defaults["embedding_dims"]))

    embedding_dims = int(
        existing_embedding_dims
        if preserve_existing_dims and existing_embedding_dims
        else configured_embedding_dims
    )
    embedder_provider = provider
    if (
        runtime_provider == "mock"
        or (
            preserve_existing_dims
            and existing_embedding_dims
            and existing_embedding_dims != configured_embedding_dims
        )
    ):
        embedder_provider = "simple"

    llm_cfg = (
        {}
        if runtime_provider == "mock"
        else {"model": llm_model, "temperature": 0.1, "max_tokens": 1024}
    )
    embedder_cfg = (
        {"embedding_dims": embedding_dims}
        if embedder_provider == "simple"
        else {"model": embedder_model, "embedding_dims": embedding_dims}
    )
    if api_key:
        llm_cfg["api_key"] = api_key
        if embedder_provider != "simple":
            embedder_cfg["api_key"] = api_key

    history_db_path = os.path.join(config_dir, "history.db")

    memory_config = MemoryConfig(
        vector_store=_resolve_vector_store_config(config, config_dir, embedding_dims),
        llm=LLMConfig(provider=runtime_provider, config=llm_cfg),
        embedder=EmbedderConfig(provider=embedder_provider, config=embedder_cfg),
        history_db_path=history_db_path,
        embedding_model_dims=embedding_dims,
        fade=FadeMemConfig(enable_forgetting=True),
    )

    return FullMemory(memory_config)
