"""Ollama embedding provider for local embeddings."""

import os
from typing import List, Optional

from dhee.embeddings.base import BaseEmbedder


class OllamaEmbedder(BaseEmbedder):
    """Embedding provider using Ollama for local embeddings.

    Supports embedding models available through Ollama (nomic-embed-text,
    mxbai-embed-large, all-minilm, etc.). No API key required.
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.host = self.config.get("host") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.model = self.config.get("model", "nomic-embed-text")

        self._client = None
        self._init_client()

    def _init_client(self):
        """Initialize the Ollama client."""
        try:
            import ollama
            self._client = ollama.Client(host=self.host)
        except ImportError as exc:
            raise ImportError(
                "Install ollama package to use OllamaEmbedder: pip install ollama"
            ) from exc

    def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        """Generate embeddings using Ollama.

        Args:
            text: The text to embed.
            memory_action: Optional action context (unused for Ollama).

        Returns:
            List of floats representing the embedding vector.
        """
        if self._client is None:
            self._init_client()

        try:
            response = self._client.embed(
                model=self.model,
                input=text,
            )
            # Response can be {"embeddings": [[...]]} or {"embedding": [...]}
            embeddings = response.get("embeddings")
            if embeddings and isinstance(embeddings, list) and len(embeddings) > 0:
                return embeddings[0]
            embedding = response.get("embedding")
            if embedding:
                return embedding
            return []
        except Exception as e:
            if "connection" in str(e).lower():
                raise ConnectionError(
                    f"Cannot connect to Ollama at {self.host}. "
                    "Make sure Ollama is running: https://ollama.ai"
                ) from e
            raise
