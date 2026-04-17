#!/usr/bin/env bash
# Dhee v3.4.0 — LongMemEval retrieval benchmark (0..500)
# NVIDIA embedder + reranker, no answer generation.

set -euo pipefail

REPO="/Users/chitranjanmalviya/Desktop/Dhee"
RUN_DIR="$REPO/runs/longmemeval_v3.4.0_nvidia_retrieval_0_500"
PY="$REPO/.venv-dhee/bin/python"

# Load NVIDIA_API_KEY from .env
set -a
source "$REPO/.env"
set +a

exec "$PY" -u -m dhee.benchmarks.longmemeval \
    --dataset-path "$REPO/data/longmemeval/longmemeval_s_cleaned.json" \
    --output-jsonl "$RUN_DIR/predictions.jsonl" \
    --retrieval-jsonl "$RUN_DIR/retrieval.jsonl" \
    --include-debug-fields \
    --user-id longmemeval_v34_nvidia \
    --start-index 0 \
    --end-index 500 \
    --top-k 10 \
    --embedder-provider nvidia \
    --enable-rerank \
    --rerank-model nvidia/llama-3.2-nv-rerankqa-1b-v2 \
    --retrieval-only \
    --print-every 1
