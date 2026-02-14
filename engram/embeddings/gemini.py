import logging
import os
from typing import List, Optional

from engram.embeddings.base import BaseEmbedder

logger = logging.getLogger(__name__)


class GeminiEmbedder(BaseEmbedder):
    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.api_key = self.config.get("api_key") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API key not provided. Set GEMINI_API_KEY or pass api_key in config.")

        self.model = self.config.get("model", "gemini-embedding-001")

        self._client_type = None
        self._client = None
        self._genai = None

        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            self._client_type = "generativeai"
            self._genai = genai
        except ImportError:
            try:
                from google import genai

                self._client_type = "genai"
                self._client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                raise ImportError(
                    "Install google-generativeai or google-genai to use GeminiEmbedder"
                ) from exc

    def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        try:
            if self._client_type == "generativeai":
                response = self._genai.embed_content(
                    model=self.model,
                    content=text,
                )
                embedding = response.get("embedding") if isinstance(response, dict) else getattr(response, "embedding", None)
                if not embedding:
                    raise RuntimeError(f"Gemini embedding returned empty result (model={self.model})")
                return embedding

            if self._client_type == "genai":
                response = self._client.models.embed_content(
                    model=self.model,
                    contents=text,
                )
                return _extract_embedding_from_response(response)

            raise RuntimeError("Gemini embedder not initialized")
        except RuntimeError:
            raise
        except Exception as exc:
            logger.error("Gemini embedding failed (model=%s): %s", self.model, exc)
            raise RuntimeError(
                f"Gemini embedding failed (model={self.model}): {exc}"
            ) from exc

    def embed_batch(
        self, texts: List[str], memory_action: Optional[str] = None
    ) -> List[List[float]]:
        """Batch embedding for Gemini — uses batch_embed_contents when available."""
        if not texts:
            return []
        if len(texts) == 1:
            return [self.embed(texts[0], memory_action=memory_action)]

        try:
            if self._client_type == "generativeai":
                # google-generativeai supports batch via embed_content with list
                response = self._genai.embed_content(
                    model=self.model,
                    content=texts,
                )
                embedding = response.get("embedding") if isinstance(response, dict) else getattr(response, "embedding", None)
                if embedding and isinstance(embedding, list) and len(embedding) == len(texts):
                    # Check if it's a list of lists (batch) vs single list
                    if embedding and isinstance(embedding[0], list):
                        return embedding
                # Fallback to sequential
                return [self.embed(t, memory_action=memory_action) for t in texts]

            if self._client_type == "genai":
                response = self._client.models.embed_content(
                    model=self.model,
                    contents=texts,
                )
                embeddings = getattr(response, "embeddings", None)
                if embeddings and isinstance(embeddings, list):
                    results = []
                    for emb in embeddings:
                        vector = getattr(emb, "values", None) or getattr(emb, "embedding", None)
                        if vector:
                            results.append(vector)
                    if len(results) == len(texts):
                        return results
                # Fallback to sequential
                return [self.embed(t, memory_action=memory_action) for t in texts]

        except Exception as exc:
            logger.warning(
                "Gemini batch embedding failed, falling back to sequential: %s", exc
            )

        return [self.embed(t, memory_action=memory_action) for t in texts]


def _extract_embedding_from_response(response) -> List[float]:
    if response is None:
        raise RuntimeError("Gemini embedding response was None")
    embedding = getattr(response, "embedding", None)
    if embedding:
        return embedding
    embeddings = getattr(response, "embeddings", None)
    if embeddings and isinstance(embeddings, list):
        first = embeddings[0]
        vector = getattr(first, "values", None) or getattr(first, "embedding", None)
        if vector:
            return vector
    raise RuntimeError("Gemini embedding response contained no embedding data")
