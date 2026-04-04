import os

from dhee.simple import _detect_provider, _get_embedding_dims


def test_detect_provider_defaults_to_mock_without_keys(monkeypatch):
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

    assert _detect_provider() == "mock"


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
