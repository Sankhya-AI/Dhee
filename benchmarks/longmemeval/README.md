# LongMemEval — Dhee retrieval benchmark

Full 500-question LongMemEval-S retrieval-only run. Numbers here are
reproducible from this directory; the full per-question retrieval output
is committed as `retrieval.jsonl` as proof.

## Result

| Metric | Score |
|:-------|:------|
| Recall@1  | **94.80%** |
| Recall@3  | **99.00%** |
| Recall@5  | **99.40%** |
| Recall@10 | **99.80%** |

500 questions, no cherry-picking, no held-out split. `any@k` = at least one
gold session in the top-k (standard LongMemEval retrieval metric).
Stricter `all@k` (every gold session in top-k) is also in `metrics.json`.

## Stack

- Embedder: `nvidia/llama-nemotron-embed-vl-1b-v2`
- Reranker: `nvidia/llama-3.2-nv-rerankqa-1b-v2`
- Top-k: 10
- Mode: `--retrieval-only` (pure retrieval; no answer generation)
- Dhee version: 3.4.0

## Comparison

Published LongMemEval retrieval numbers from other memory systems
(each linked to its source README so the figures can be verified):

| System | R@1 | R@3 | R@5 | R@10 | Source |
|:-------|:----|:----|:----|:-----|:-------|
| **Dhee v3.4.0** | **94.8%** | **99.0%** | **99.4%** | **99.8%** | this dir |
| MemPalace (raw) | — | — | 96.6% | — | [MemPalace README](https://github.com/MemPalace/mempalace#benchmarks) |
| MemPalace (hybrid v4, held-out 450) | — | — | 98.4% | — | same |
| agentmemory | — | — | 95.2% | 98.6% | [agentmemory README](https://github.com/rohitg00/agentmemory#benchmarks) |

Caveats worth knowing:
- MemPalace's 98.4% is on a 450-question held-out split, not the full 500.
- agentmemory's 95.2% uses a local `all-MiniLM-L6-v2` embedder (no API key).
  Dhee's stack uses NVIDIA hosted embed+rerank models.
- LongMemEval-S is the same underlying dataset across all three.

## Reproduce

```bash
# from repo root, with .env containing NVIDIA_API_KEY
bash benchmarks/longmemeval/command.sh
```

The script writes `retrieval.jsonl` + `predictions.jsonl` to its own
`RUN_DIR`. Recompute metrics from the committed `retrieval.jsonl`:

```python
import json
ks = [1, 3, 5, 10]
any_hits = {k: 0 for k in ks}
n = 0
with open("benchmarks/longmemeval/retrieval.jsonl") as f:
    for line in f:
        r = json.loads(line)
        top = r["retrieved_session_ids"]
        gold = set(r["answer_session_ids"])
        if not gold:
            continue
        n += 1
        for k in ks:
            if set(top[:k]) & gold:
                any_hits[k] += 1
for k in ks:
    print(f"R@{k} = {any_hits[k]/n:.4f}")
```

## Files

- `command.sh` — exact command used to produce the run
- `metrics.json` — summary metrics (both `any@k` and `all@k`)
- `retrieval.jsonl` — 500 per-question records with top-10 IDs, gold IDs,
  gold rank by similarity/rerank/composite, and rerank application flag
