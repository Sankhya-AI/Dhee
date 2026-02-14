"""Async embedder base class and implementations."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import List, Optional


class AsyncBaseEmbedder(ABC):
    """Base class for async embedding providers."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    @abstractmethod
    async def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        """Generate embedding for text."""
        pass

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts. Override for batch optimization."""
        import asyncio
        return await asyncio.gather(*[self.embed(text) for text in texts])


class AsyncGeminiEmbedder(AsyncBaseEmbedder):
    """Async Gemini embedding provider."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.api_key = self.config.get("api_key") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API key not provided")

        self.model = self.config.get("model", "gemini-embedding-001")

        self._client_type = None
        self._client = None
        self._genai = None

        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            self._client_type = "generativeai"
            self._genai = genai
        except Exception:
            try:
                from google import genai
                self._client_type = "genai"
                self._client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                raise ImportError("Install google-generativeai to use AsyncGeminiEmbedder") from exc

    async def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        """Generate embedding asynchronously."""
        if self._client_type == "generativeai":
            # google-generativeai doesn't have async embed, use sync in thread
            import asyncio
            response = await asyncio.to_thread(
                self._genai.embed_content,
                model=self.model,
                content=text,
            )
            embedding = response.get("embedding") if isinstance(response, dict) else getattr(response, "embedding", None)
            return embedding or []

        if self._client_type == "genai":
            response = await self._client.aio.models.embed_content(
                model=self.model,
                contents=text,
            )
            return _extract_embedding_from_response(response)

        return []


class AsyncOpenAIEmbedder(AsyncBaseEmbedder):
    """Async OpenAI embedding provider."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.api_key = self.config.get("api_key") or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not provided")

        self.model = self.config.get("model", "text-embedding-3-small")

        try:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key)
        except ImportError as exc:
            raise ImportError("Install openai to use AsyncOpenAIEmbedder") from exc

    async def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        """Generate embedding asynchronously."""
        response = await self._client.embeddings.create(
            model=self.model,
            input=text,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts in a single API call."""
        response = await self._client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [item.embedding for item in response.data]


def _extract_embedding_from_response(response) -> List[float]:
    """Extract embedding from Gemini response."""
    if response is None:
        return []
    embedding = getattr(response, "embedding", None)
    if embedding:
        return embedding
    embeddings = getattr(response, "embeddings", None)
    if embeddings and isinstance(embeddings, list):
        first = embeddings[0]
        vector = getattr(first, "values", None) or getattr(first, "embedding", None)
        if vector:
            return vector
    return []
