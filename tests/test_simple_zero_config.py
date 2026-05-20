import os
import sqlite3

from dhee.simple import (
    DEFAULT_NVIDIA_EMBEDDER_MODEL,
    DEFAULT_NVIDIA_LLM_MODEL,
    Engram,
    _detect_provider,
    _existing_sqlite_vec_dims,
    _get_embedding_dims,
)


def test_detect_provider_defaults_to_nvidia_without_keys(monkeypatch):
    for key in (
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "NVIDIA_API_KEY",
        "NVIDIA_QWEN_API_KEY",
        "NVIDIA_EMBEDDING_API_KEY",
        "NVIDIA_EMBED_API_KEY",
        "NVIDIA_LLAMA_4_MAV_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    assert _detect_provider() == "nvidia"


def test_embedding_dims_cover_mock_and_nvidia():
    assert _get_embedding_dims("mock") == 384
    assert _get_embedding_dims("nvidia") == 2048


def test_detect_provider_accepts_nvidia_alias_keys(monkeypatch):
    for key in (
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "NVIDIA_API_KEY",
        "NVIDIA_QWEN_API_KEY",
        "NVIDIA_EMBEDDING_API_KEY",
        "NVIDIA_EMBED_API_KEY",
        "NVIDIA_LLAMA_4_MAV_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("NVIDIA_EMBED_API_KEY", "test-key")
    assert _detect_provider() == "nvidia"


def test_engram_uses_nvidia_models_when_provider_is_nvidia(monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")

    class FakeFullMemory:
        def __init__(self, config):
            self.config = config

    monkeypatch.setattr("dhee.simple.FullMemory", FakeFullMemory)

    engram = Engram(provider="nvidia", in_memory=True, data_dir=tmp_path)

    assert engram.memory.config.llm.config["model"] == DEFAULT_NVIDIA_LLM_MODEL
    assert engram.memory.config.embedder.config["model"] == DEFAULT_NVIDIA_EMBEDDER_MODEL


def test_engram_preserves_existing_sqlite_vec_dimensions(monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("DHEE_PRESERVE_EXISTING_EMBEDDING_DIMS", "true")
    db_path = tmp_path / "sqlite_vec.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE vec_dhee (embedding float[384])")
        conn.execute("CREATE TABLE payload_dhee (rowid INTEGER PRIMARY KEY, uuid TEXT, payload TEXT)")
        conn.commit()
    finally:
        conn.close()

    class FakeFullMemory:
        def __init__(self, config):
            self.config = config

    monkeypatch.setattr("dhee.simple.FullMemory", FakeFullMemory)

    engram = Engram(provider="nvidia", data_dir=tmp_path)

    assert _existing_sqlite_vec_dims(db_path, "dhee") == 384
    assert engram.memory.config.llm.provider == "nvidia"
    assert engram.memory.config.llm.config["model"] == DEFAULT_NVIDIA_LLM_MODEL
    assert engram.memory.config.embedder.provider == "simple"
    assert engram.memory.config.embedder.config["embedding_dims"] == 384
    assert engram.memory.config.vector_store.provider == "zvec"
    assert engram.memory.config.vector_store.config["path"] == str(tmp_path / "zvec")
    assert engram.memory.config.vector_store.config["embedding_model_dims"] == 384


def test_engram_uses_dimmed_zvec_collection_when_base_collection_is_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr("dhee.simple._history_embedding_dims", lambda _data_dir: 2048)
    monkeypatch.setattr(
        "dhee.simple._existing_zvec_dims",
        lambda _path, collection: 384 if collection == "dhee" else 2048 if collection == "dhee_nvidia_2048" else None,
    )

    class FakeFullMemory:
        def __init__(self, config):
            self.config = config

    monkeypatch.setattr("dhee.simple.FullMemory", FakeFullMemory)

    engram = Engram(provider="nvidia", data_dir=tmp_path)

    assert engram.memory.config.vector_store.config["collection_name"] == "dhee_nvidia_2048"
    assert engram.memory.config.vector_store.config["embedding_model_dims"] == 2048
