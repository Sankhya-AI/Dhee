import logging
from typing import List, Optional

from dhee.embeddings.base import BaseEmbedder

logger = logging.getLogger(__name__)


class OpenAIEmbedder(BaseEmbedder):
    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ImportError("openai package is required for OpenAIEmbedder") from exc
        timeout = self.config.get("timeout", 60)
        client_kwargs = {"timeout": timeout}
        if self.config.get("api_key"):
            client_kwargs["api_key"] = self.config["api_key"]
        if self.config.get("base_url"):
            client_kwargs["base_url"] = self.config["base_url"]
        self.client = OpenAI(**client_kwargs)
        self.model = self.config.get("model", "text-embedding-3-large")
        self.embedding_dims = self.config.get("embedding_dims")

    def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        try:
            kwargs = {"model": self.model, "input": text}
            if self.embedding_dims:
                kwargs["dimensions"] = int(self.embedding_dims)
            response = self.client.embeddings.create(**kwargs)
            return response.data[0].embedding
        except Exception as exc:
            logger.error("OpenAI embedding failed (model=%s): %s", self.model, exc)
            raise RuntimeError(
                f"OpenAI embedding failed (model={self.model}): {exc}"
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
            kwargs = {"model": self.model, "input": texts}
            if self.embedding_dims:
                kwargs["dimensions"] = int(self.embedding_dims)
            response = self.client.embeddings.create(**kwargs)
            # Response data is sorted by index
            sorted_data = sorted(response.data, key=lambda d: d.index)
            return [d.embedding for d in sorted_data]
        except Exception as exc:
            logger.warning(
                "OpenAI batch embedding failed, falling back to sequential: %s", exc
            )
            return [self.embed(t, memory_action=memory_action) for t in texts]
