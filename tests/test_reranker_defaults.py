from dhee.memory.reranker import NvidiaReranker


def test_nvidia_reranker_defaults_to_nemotron_vl(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")

    reranker = NvidiaReranker()

    assert reranker.model == "nvidia/llama-nemotron-rerank-vl-1b-v2"
    assert reranker.url.endswith(
        "/retrieval/nvidia/llama-nemotron-rerank-vl-1b-v2/reranking"
    )
