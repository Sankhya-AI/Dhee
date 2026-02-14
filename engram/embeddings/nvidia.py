import logging
import os
from typing import List, Optional

from engram.embeddings.base import BaseEmbedder

logger = logging.getLogger(__name__)


class NvidiaEmbedder(BaseEmbedder):
    """Embedding provider for NVIDIA API (OpenAI-compatible). Default model: nv-embed-v1."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ImportError("openai package is required for NvidiaEmbedder") from exc

        api_key = (
            self.config.get("api_key")
            or os.getenv("NVIDIA_EMBEDDING_API_KEY")
            or os.getenv("NVIDIA_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "NVIDIA API key required. Set config['api_key'], "
                "NVIDIA_EMBEDDING_API_KEY, or NVIDIA_API_KEY env var."
            )

        base_url = self.config.get("base_url", "https://integrate.api.nvidia.com/v1")
        timeout = self.config.get("timeout", 60)
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = self.config.get("model", "nvidia/nv-embed-v1")

    def _extra_body(self, memory_action: Optional[str] = None) -> dict:
        """Build extra_body for E5/embedqa models."""
        if "e5" in self.model or "embedqa" in self.model:
            input_type = "query" if memory_action in ("search", "forget") else "passage"
            return {"input_type": input_type, "truncate": "NONE"}
        return {}

    def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        try:
            extra_body = self._extra_body(memory_action)
            response = self.client.embeddings.create(
                input=[text],
                model=self.model,
                encoding_format="float",
                **({"extra_body": extra_body} if extra_body else {}),
            )
            return response.data[0].embedding
        except Exception as exc:
            logger.error("NVIDIA embedding failed (model=%s): %s", self.model, exc)
            raise RuntimeError(
                f"NVIDIA embedding failed (model={self.model}): {exc}"
            ) from exc

    def embed_batch(
        self, texts: List[str], memory_action: Optional[str] = None
    ) -> List[List[float]]:
        """Native batch embedding — single API call for N texts."""
        if not texts:
            return []
        if len(texts) == 1:
            return [self.embed(texts[0], memory_action=memory_action)]
        try:
            extra_body = self._extra_body(memory_action)
            response = self.client.embeddings.create(
                input=texts,
                model=self.model,
                encoding_format="float",
                **({"extra_body": extra_body} if extra_body else {}),
            )
            sorted_data = sorted(response.data, key=lambda d: d.index)
            return [d.embedding for d in sorted_data]
        except Exception as exc:
            logger.warning(
                "NVIDIA batch embedding failed, falling back to sequential: %s", exc
            )
            return [self.embed(t, memory_action=memory_action) for t in texts]
