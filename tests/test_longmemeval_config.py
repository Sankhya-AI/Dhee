import sys

import pytest

from dhee.benchmarks.longmemeval import (
    DEFAULT_NVIDIA_EMBEDDER_MODEL,
    DEFAULT_NVIDIA_EMBEDDING_DIMS,
    DEFAULT_NVIDIA_RERANK_MODEL,
    parse_args,
)


def test_longmemeval_nvidia_defaults_match_repro_stack(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "longmemeval",
            "--dataset-path",
            "dataset.json",
            "--output-jsonl",
            "predictions.jsonl",
            "--embedder-provider",
            "nvidia",
        ],
    )

    args = parse_args()

    assert DEFAULT_NVIDIA_EMBEDDER_MODEL == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert DEFAULT_NVIDIA_RERANK_MODEL == "nvidia/llama-nemotron-rerank-vl-1b-v2"
    assert args.embedding_dims == DEFAULT_NVIDIA_EMBEDDING_DIMS == 2048


def test_longmemeval_rejects_unsupported_nemotron_dimension(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "longmemeval",
            "--dataset-path",
            "dataset.json",
            "--output-jsonl",
            "predictions.jsonl",
            "--embedder-provider",
            "nvidia",
            "--embedding-dims",
            "1536",
        ],
    )

    with pytest.raises(SystemExit):
        parse_args()
