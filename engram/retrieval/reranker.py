"""Neural reranker for second-stage retrieval refinement.

Uses a cross-encoder model to re-score (query, passage) pairs with full
attention, producing much more accurate relevance scores than embedding
cosine similarity alone.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class NvidiaReranker:
    """NVIDIA NIM reranker using the /reranking endpoint."""

    _DEFAULT_URL = (
        "https://ai.api.nvidia.com/v1/retrieval/"
        "nvidia/llama-3_2-nv-rerankqa-1b-v2/reranking"
    )

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self.model = config.get("model", "nvidia/llama-3.2-nv-rerankqa-1b-v2")
        api_key_env = config.get("api_key_env", "NVIDIA_API_KEY")
        self.api_key = config.get("api_key") or os.getenv(api_key_env)
        if not self.api_key:
            raise ValueError(
                f"NVIDIA API key required for reranker. Set config['api_key'] or {api_key_env} env var."
            )
        # Build URL from model name: replace / with _ and dots with _
        # e.g. nvidia/llama-3.2-nv-rerankqa-1b-v2 -> nvidia/llama-3_2-nv-rerankqa-1b-v2
        model_path = self.model.replace(".", "_")
        self.url = config.get(
            "url",
            f"https://ai.api.nvidia.com/v1/retrieval/{model_path}/reranking",
        )
        self.timeout = config.get("timeout", 30)
        self.max_retries = config.get("max_retries", 2)

    def rerank(
        self,
        query: str,
        passages: List[str],
        top_n: int = 0,
    ) -> List[Dict[str, Any]]:
        """Rerank passages against a query.

        Args:
            query: The search query.
            passages: List of passage texts to rerank.
            top_n: Number of top results to return (0 = return all, re-sorted).

        Returns:
            List of dicts with keys: index (original position), logit, text.
            Sorted by logit descending.
        """
        if not passages:
            return []
        if len(passages) == 1:
            return [{"index": 0, "logit": 0.0, "text": passages[0]}]

        payload = {
            "model": self.model,
            "query": {"text": query},
            "passages": [{"text": p} for p in passages],
        }
        if top_n > 0:
            payload["top_n"] = top_n

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                t0 = time.monotonic()
                resp = requests.post(
                    self.url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                elapsed_ms = (time.monotonic() - t0) * 1000
                resp.raise_for_status()
                data = resp.json()

                rankings = data.get("rankings", [])
                results = []
                for r in rankings:
                    idx = r.get("index", 0)
                    results.append({
                        "index": idx,
                        "logit": r.get("logit", 0.0),
                        "text": passages[idx] if idx < len(passages) else "",
                    })
                results.sort(key=lambda x: x["logit"], reverse=True)
                logger.debug(
                    "Reranked %d passages in %.0fms (top logit=%.2f)",
                    len(passages), elapsed_ms,
                    results[0]["logit"] if results else 0.0,
                )
                return results

            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    delay = min(2 ** attempt, 4)
                    logger.warning(
                        "Reranker retry %d/%d after %ss: %s",
                        attempt + 1, self.max_retries, delay, exc,
                    )
                    time.sleep(delay)
                else:
                    logger.error("Reranker failed after %d attempts: %s", self.max_retries + 1, exc)

        raise RuntimeError(f"Reranker failed: {last_exc}") from last_exc


def create_reranker(config: Optional[Dict[str, Any]] = None) -> Optional[NvidiaReranker]:
    """Factory: create a reranker from config, or return None if disabled."""
    if not config:
        return None
    provider = config.get("provider", "nvidia")
    if provider == "nvidia":
        return NvidiaReranker(config)
    logger.warning("Unknown reranker provider: %s", provider)
    return None
