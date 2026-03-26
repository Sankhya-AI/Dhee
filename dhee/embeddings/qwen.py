"""Qwen3-Embedding-0.6B embedder — local CPU-native embedding.

0.6B params, flexible 32-1024 dims, MTEB English 70.70.
Supports sentence-transformers or GGUF/Ollama backends.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from dhee.embeddings.base import BaseEmbedder

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"
_DEFAULT_DIMS = 1024


class QwenEmbedder(BaseEmbedder):
    """Qwen3-Embedding-0.6B via sentence-transformers or Ollama."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.model_name = self.config.get("model", _DEFAULT_MODEL)
        self.dims = self.config.get("embedding_dims", _DEFAULT_DIMS)
        self.backend = self.config.get("backend", "sentence_transformers")
        self.device = self.config.get("device", "cpu")
        self._model = None

    def _ensure_model(self):
        """Lazy-load the embedding model."""
        if self._model is not None:
            return

        if self.backend == "ollama":
            self._init_ollama()
        else:
            self._init_sentence_transformers()

    def _init_sentence_transformers(self):
        """Initialize via sentence-transformers."""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers is required for QwenEmbedder. "
                "Install with: pip install sentence-transformers"
            )

        logger.info("Loading Qwen3 embedding model: %s", self.model_name)
        self._model = SentenceTransformer(
            self.model_name,
            device=self.device,
            truncate_dim=self.dims,
        )
        logger.info("Qwen3 embedder loaded (%d dims)", self.dims)

    def _init_ollama(self):
        """Initialize via Ollama."""
        try:
            import ollama as ollama_client
            self._model = ollama_client
            # Verify model is available
            self._model.embeddings(model=self.model_name, prompt="test")
            logger.info("Qwen3 embedder via Ollama: %s", self.model_name)
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize Qwen3 embedder via Ollama: {e}. "
                f"Make sure Ollama is running and the model is pulled."
            )

    def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        """Embed a single text."""
        self._ensure_model()

        if not text or not text.strip():
            return [0.0] * self.dims

        # Truncate long texts
        if len(text) > 8000:
            text = text[:8000]

        if self.backend == "ollama":
            return self._embed_ollama(text)
        return self._embed_st(text)

    def embed_batch(
        self, texts: List[str], memory_action: Optional[str] = None
    ) -> List[List[float]]:
        """Batch embed multiple texts efficiently."""
        self._ensure_model()

        if not texts:
            return []

        # Truncate and clean
        clean_texts = []
        for t in texts:
            t = (t or "").strip()
            if not t:
                t = " "
            if len(t) > 8000:
                t = t[:8000]
            clean_texts.append(t)

        if self.backend == "ollama":
            return [self._embed_ollama(t) for t in clean_texts]
        return self._embed_st_batch(clean_texts)

    def _embed_st(self, text: str) -> List[float]:
        """Embed via sentence-transformers."""
        embedding = self._model.encode(
            text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embedding.tolist()

    def _embed_st_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch embed via sentence-transformers."""
        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=32,
        )
        return [e.tolist() for e in embeddings]

    def _embed_ollama(self, text: str) -> List[float]:
        """Embed via Ollama."""
        response = self._model.embeddings(
            model=self.model_name,
            prompt=text,
        )
        embedding = response.get("embedding", [])
        # Truncate to desired dims
        if len(embedding) > self.dims:
            embedding = embedding[:self.dims]
        return embedding
