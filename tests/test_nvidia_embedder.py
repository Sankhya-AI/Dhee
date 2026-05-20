import types

from dhee.embeddings.nvidia import NvidiaEmbedder


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
