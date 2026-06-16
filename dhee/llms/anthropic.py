import logging
import os
from typing import Optional

import requests

from dhee.llms.base import BaseLLM

logger = logging.getLogger(__name__)


class AnthropicLLM(BaseLLM):
    """Claude Messages API provider.

    Anthropic does not provide a first-party same-key embedding or reranker API
    in the official Claude API docs. This class covers only the LLM lane; Dhee
    embeddings must use a local/simple provider or another explicit profile.
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.api_key = (
            self.config.get("api_key")
            or os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("CLAUDE_API_KEY")
        )
        if not self.api_key:
            raise ValueError(
                "Anthropic API key not provided. Set ANTHROPIC_API_KEY or pass api_key in config."
            )
        self.model = self.config.get("model", "claude-opus-4-7")
        self.temperature = self.config.get("temperature", 0.1)
        self.max_tokens = int(self.config.get("max_tokens", 4096))
        self.timeout = float(self.config.get("timeout", 60))
        self.base_url = str(
            self.config.get("base_url") or "https://api.anthropic.com/v1"
        ).rstrip("/")

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            response = requests.post(
                f"{self.base_url}/messages",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            return _extract_text(data)
        except Exception as exc:
            logger.error("Anthropic LLM generate failed (model=%s): %s", self.model, exc)
            raise RuntimeError(
                f"Anthropic LLM generation failed (model={self.model}): {exc}"
            ) from exc


def _extract_text(data: dict) -> str:
    chunks = []
    for item in data.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text") or ""))
    return "".join(chunks)
