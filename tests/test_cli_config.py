import json
import sqlite3

from dhee.cli_config import get_default_config, get_memory_instance, save_config


def test_default_config_is_nvidia_zvec():
    config = get_default_config()

    assert config["provider"] == "nvidia"
    assert config["embedder_model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert config["embedding_dims"] == 2048
    assert config["vector_store"]["provider"] == "zvec"
    assert config["vector_store"]["config"]["collection_name"] == "dhee"


class FakeFullMemory:
    def __init__(self, config):
        self.config = config


def _write_history_embedding(data_dir, dims):
    conn = sqlite3.connect(data_dir / "history.db")
    try:
        conn.execute(
            "CREATE TABLE memories (id TEXT, embedding TEXT, tombstone INTEGER DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO memories (id, embedding, tombstone) VALUES (?, ?, 0)",
            ("mem-1", json.dumps([0.0] * dims)),
        )
        conn.commit()
    finally:
        conn.close()


def test_get_memory_instance_defaults_to_zvec_and_existing_history_dims(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr("dhee.cli_config.get_api_key", lambda _provider: None)
    monkeypatch.setattr("dhee.memory.main.FullMemory", FakeFullMemory)
    _write_history_embedding(tmp_path, 384)

    memory = get_memory_instance(
        {
            "provider": "nvidia",
            "embedding_dims": 768,
            "preserve_existing_embedding_dims": True,
        }
    )
    config = memory.config

    assert config.llm.provider == "mock"
    assert config.vector_store.provider == "zvec"
    assert config.vector_store.config["path"] == str(tmp_path / "zvec")
    assert config.vector_store.config["collection_name"] == "dhee"
    assert config.vector_store.config["embedding_model_dims"] == 384
    assert config.embedding_model_dims == 384
    assert config.embedder.provider == "simple"
    assert config.embedder.config["embedding_dims"] == 384


def test_get_memory_instance_uses_nvidia_default_dims_when_not_preserving_history(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr("dhee.memory.main.FullMemory", FakeFullMemory)
    _write_history_embedding(tmp_path, 384)

    memory = get_memory_instance({"provider": "nvidia", "embedding_dims": 2048})
    config = memory.config

    assert config.llm.provider == "nvidia"
    assert config.embedder.provider == "nvidia"
    assert config.embedder.config["embedding_dims"] == 2048
    assert config.vector_store.config["embedding_model_dims"] == 2048
    assert config.embedding_model_dims == 2048


def test_get_memory_instance_honors_explicit_sqlite_vec(monkeypatch, tmp_path):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("dhee.memory.main.FullMemory", FakeFullMemory)

    memory = get_memory_instance(
        {
            "provider": "ollama",
            "embedding_dims": 768,
            "vector_store_provider": "sqlite_vec",
            "vector_store_config": {"collection_name": "legacy"},
        }
    )
    config = memory.config

    assert config.vector_store.provider == "sqlite_vec"
    assert config.vector_store.config["path"] == str(tmp_path / "sqlite_vec.db")
    assert config.vector_store.config["collection_name"] == "legacy"
    assert config.vector_store.config["embedding_model_dims"] == 768


def test_cli_model_free_memory_uses_configured_dims_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("dhee.memory.main.FullMemory", FakeFullMemory)
    _write_history_embedding(tmp_path, 384)
    save_config(
        {
            "provider": "nvidia",
            "embedder_model": "nvidia/llama-nemotron-embed-vl-1b-v2",
            "embedding_dims": 2048,
            "preserve_existing_embedding_dims": False,
            "vector_store": {
                "provider": "zvec",
                "config": {
                    "path": str(tmp_path / "zvec"),
                    "collection_name": "dhee_nvidia_2048",
                    "embedding_model_dims": 2048,
                },
            },
        }
    )

    from dhee import cli

    memory = cli._get_model_free_memory(persistent_vectors=True)
    config = memory.config

    assert config.embedding_model_dims == 2048
    assert config.embedder.config["embedding_dims"] == 2048
    assert config.vector_store.provider == "zvec"
    assert config.vector_store.config["collection_name"] == "dhee_nvidia_2048"
    assert config.vector_store.config["embedding_model_dims"] == 2048


def test_cli_model_free_memory_preserves_legacy_dims_when_requested(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("dhee.memory.main.FullMemory", FakeFullMemory)
    _write_history_embedding(tmp_path, 384)
    save_config(
        {
            "provider": "nvidia",
            "embedding_dims": 2048,
            "preserve_existing_embedding_dims": True,
            "vector_store": {
                "provider": "zvec",
                "config": {
                    "path": str(tmp_path / "zvec"),
                    "collection_name": "legacy",
                    "embedding_model_dims": 2048,
                },
            },
        }
    )

    from dhee import cli

    memory = cli._get_model_free_memory(persistent_vectors=True)
    config = memory.config

    assert config.embedding_model_dims == 384
    assert config.embedder.config["embedding_dims"] == 384
    assert config.vector_store.config["embedding_model_dims"] == 384


def test_cli_vector_store_helper_uses_configured_dims_by_default(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    _write_history_embedding(tmp_path, 384)
    save_config(
        {
            "provider": "nvidia",
            "embedding_dims": 2048,
            "preserve_existing_embedding_dims": False,
            "vector_store": {
                "provider": "zvec",
                "config": {
                    "path": str(tmp_path / "zvec"),
                    "collection_name": "dhee_nvidia_2048",
                    "embedding_model_dims": 2048,
                },
            },
        }
    )
    captured = {}

    def fake_create(provider, config):
        captured["provider"] = provider
        captured["config"] = config
        return object()

    monkeypatch.setattr("dhee.utils.factory.VectorStoreFactory.create", fake_create)

    from dhee import cli

    cli._get_vector_store()

    assert captured["provider"] == "zvec"
    assert captured["config"]["collection_name"] == "dhee_nvidia_2048"
    assert captured["config"]["embedding_model_dims"] == 2048
