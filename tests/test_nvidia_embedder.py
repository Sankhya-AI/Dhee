import types

from dhee.embeddings.nvidia import NvidiaEmbedder
from dhee.llms.nvidia import NvidiaLLM


class _FakeEmbedding:
    embedding = [0.1, 0.2]
    index = 0


class _FakeEmbeddingsClient:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(data=[_FakeEmbedding()])


def test_nvidia_embedder_uses_nemotron_default_and_dimensions(monkeypatch):
    fake_embeddings = _FakeEmbeddingsClient()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.embeddings = fake_embeddings

    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    embedder = NvidiaEmbedder({"embedding_dims": 384})
    result = embedder.embed("hello", memory_action="search")

    assert result == [0.1, 0.2]
    assert embedder.model == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert fake_embeddings.calls[0]["model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert fake_embeddings.calls[0]["dimensions"] == 384
    assert fake_embeddings.calls[0]["extra_body"]["input_type"] == "query"


def test_nvidia_embedder_reads_stored_key_when_env_is_absent(monkeypatch):
    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.embeddings = _FakeEmbeddingsClient()

    for key in (
        "NVIDIA_API_KEY",
        "NVIDIA_EMBEDDING_API_KEY",
        "NVIDIA_EMBED_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("dhee.cli_config.get_api_key", lambda provider: "stored-key")
    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    embedder = NvidiaEmbedder({"embedding_dims": 2048})

    assert embedder.client.kwargs["api_key"] == "stored-key"


def test_nvidia_llm_reads_stored_key_when_env_is_absent(monkeypatch):
    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    for key in (
        "NVIDIA_API_KEY",
        "NVIDIA_QWEN_API_KEY",
        "NVIDIA_LLAMA_4_MAV_API_KEY",
        "LLAMA_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("dhee.cli_config.get_api_key", lambda provider: "stored-key")
    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    llm = NvidiaLLM()

    assert llm.client.kwargs["api_key"] == "stored-key"
