"""Ollama LLM provider for local model inference."""

import os
from typing import Optional

from dhee.llms.base import BaseLLM


class OllamaLLM(BaseLLM):
    """LLM provider using Ollama for local model inference.

    Supports any model available through Ollama (llama3, mistral, phi, etc.).
    No API key required - runs entirely locally.
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.host = self.config.get("host") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.model = self.config.get("model", "llama3.2")
        self.temperature = self.config.get("temperature", 0.1)
        self.max_tokens = self.config.get("max_tokens", 1024)

        self._client = None
        self._init_client()

    def _init_client(self):
        """Initialize the Ollama client."""
        try:
            import ollama
            self._client = ollama.Client(host=self.host)
        except ImportError as exc:
            raise ImportError(
                "Install ollama package to use OllamaLLM: pip install ollama"
            ) from exc

    def generate(self, prompt: str) -> str:
        """Generate text using Ollama."""
        if self._client is None:
            self._init_client()

        try:
            response = self._client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                }
            )
            return response.get("response", "")
        except Exception as e:
            # Check if Ollama server is running
            if "connection" in str(e).lower():
                raise ConnectionError(
                    f"Cannot connect to Ollama at {self.host}. "
                    "Make sure Ollama is running: https://ollama.ai"
                ) from e
            raise
