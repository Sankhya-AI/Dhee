import logging
import os
from typing import List, Optional

from dhee.embeddings.base import BaseEmbedder

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
            or os.getenv("NVIDIA_EMBED_API_KEY")
            or os.getenv("NVIDIA_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "NVIDIA API key required. Set config['api_key'], "
                "NVIDIA_EMBEDDING_API_KEY, NVIDIA_EMBED_API_KEY, or NVIDIA_API_KEY env var."
            )

        base_url = self.config.get("base_url", "https://integrate.api.nvidia.com/v1")
        timeout = self.config.get("timeout", 60)
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = self.config.get("model", "nvidia/nv-embed-v1")
        default_truncate = "NONE" if "nemotron-embed" in self.model else "END"
        self.truncate = str(self.config.get("truncate", default_truncate)).upper()

    def _extra_body(self, memory_action: Optional[str] = None, count: int = 1) -> dict:
        """Build extra_body for models that need input_type differentiation.

        Args:
            memory_action: The action type (search, forget, etc.)
            count: Number of texts in the batch. nemotron-embed requires
                   modality list length to match input length.
        """
        if "e5" in self.model or "embedqa" in self.model:
            input_type = "query" if memory_action in ("search", "forget") else "passage"
            return {"input_type": input_type, "truncate": self.truncate}
        if "nemotron-embed" in self.model:
            input_type = "query" if memory_action in ("search", "forget") else "passage"
            return {"modality": ["text"] * count, "input_type": input_type, "truncate": self.truncate}
        return {}

    def _truncate_if_needed(self, text: str) -> str:
        """Truncate text to stay within model token limits.

        Model-aware defaults (conservative ~3.5 chars/token):
        - nv-embed-v1: 4096 tokens → 14000 chars
        - nemotron-embed: 8192 tokens → 26000 chars (use 24000 for safety)
        """
        if "nemotron-embed" in self.model:
            default_max = 24000
        else:
            default_max = 14000
        max_chars = int(self.config.get("max_input_chars", default_max))
        if len(text) > max_chars:
            logger.debug("Truncating input from %d to %d chars for embedding", len(text), max_chars)
            return text[:max_chars]
        return text

    def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        import time as _time
        text = self._truncate_if_needed(text)
        max_retries = int(self.config.get("max_retries", 3))
        last_exc = None
        for attempt in range(max_retries + 1):
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
                last_exc = exc
                if attempt < max_retries:
                    delay = min(2 ** attempt, 8)
                    logger.warning("NVIDIA embed retry %d/%d after %ss: %s", attempt + 1, max_retries, delay, exc)
                    _time.sleep(delay)
                else:
                    logger.error("NVIDIA embedding failed (model=%s): %s", self.model, exc)
        raise RuntimeError(
            f"NVIDIA embedding failed (model={self.model}): {last_exc}"
        ) from last_exc

    def embed_batch(
        self, texts: List[str], memory_action: Optional[str] = None
    ) -> List[List[float]]:
        """Native batch embedding — single API call for N texts."""
        if not texts:
            return []
        texts = [self._truncate_if_needed(t) for t in texts]
        if len(texts) == 1:
            return [self.embed(texts[0], memory_action=memory_action)]
        try:
            extra_body = self._extra_body(memory_action, count=len(texts))
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
