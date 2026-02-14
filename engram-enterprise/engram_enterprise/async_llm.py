"""Async LLM base class and implementations."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import List, Optional


class AsyncBaseLLM(ABC):
    """Base class for async LLM providers."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    @abstractmethod
    async def generate(self, prompt: str) -> str:
        """Generate text from a prompt."""
        pass


class AsyncGeminiLLM(AsyncBaseLLM):
    """Async Gemini LLM provider."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.api_key = self.config.get("api_key") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API key not provided")

        self.model = self.config.get("model", "gemini-2.0-flash")
        self.temperature = self.config.get("temperature", 0.1)
        self.max_tokens = self.config.get("max_tokens", 1024)

        self._client_type = None
        self._model = None
        self._client = None

        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            self._client_type = "generativeai"
            self._genai = genai
            self._model = genai.GenerativeModel(self.model)
        except Exception:
            try:
                from google import genai
                self._client_type = "genai"
                self._client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                raise ImportError("Install google-generativeai to use AsyncGeminiLLM") from exc

    async def generate(self, prompt: str) -> str:
        """Generate text asynchronously."""
        if self._client_type == "generativeai":
            response = await self._model.generate_content_async(
                prompt,
                generation_config={
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                },
            )
            return getattr(response, "text", "") or ""

        if self._client_type == "genai":
            # genai client - use aio module
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                },
            )
            return _extract_text_from_response(response)

        return ""


class AsyncOpenAILLM(AsyncBaseLLM):
    """Async OpenAI LLM provider."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.api_key = self.config.get("api_key") or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not provided")

        self.model = self.config.get("model", "gpt-4o-mini")
        self.temperature = self.config.get("temperature", 0.1)
        self.max_tokens = self.config.get("max_tokens", 1024)

        try:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key)
        except ImportError as exc:
            raise ImportError("Install openai to use AsyncOpenAILLM") from exc

    async def generate(self, prompt: str) -> str:
        """Generate text asynchronously."""
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content or ""


def _extract_text_from_response(response) -> str:
    """Extract text from Gemini response."""
    if response is None:
        return ""
    text = getattr(response, "text", None)
    if text:
        return text
    candidates = getattr(response, "candidates", None)
    if not candidates:
        return ""
    first = candidates[0]
    content = getattr(first, "content", None)
    if not content:
        return ""
    parts = getattr(content, "parts", None)
    if not parts:
        return ""
    return "".join([getattr(part, "text", "") for part in parts if getattr(part, "text", None)])
