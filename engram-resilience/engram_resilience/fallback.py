"""FallbackChain — cascading model fallback."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class FallbackChain:
    """Try each provider in order until one succeeds.

    Provides cascading fallback for both LLM generation and embedding.
    Logs failures and tracks which provider is currently active.
    """

    def __init__(self, providers: list[dict], memory: Any = None) -> None:
        """
        Args:
            providers: List of provider configs, each with "provider" and "model" keys.
                       e.g. [{"provider": "gemini", "model": "gemini-2.0-flash"},
                             {"provider": "openai", "model": "gpt-4o-mini"}]
            memory: Optional Memory instance for logging fallback events.
        """
        if not providers:
            raise ValueError("At least one provider is required")
        self._chain = providers
        self._memory = memory
        self._current_index = 0
        self._error_history: list[dict] = []
        self._fallback_count = 0
        self._llm_instances: dict[int, Any] = {}
        self._embedder_instances: dict[int, Any] = {}

    def _get_llm(self, index: int) -> Any:
        """Lazy-create an LLM instance for the given provider index."""
        if index not in self._llm_instances:
            from engram.utils.factory import LLMFactory
            config = self._chain[index]
            self._llm_instances[index] = LLMFactory.create(
                provider=config["provider"],
                config={"model": config.get("model", ""), **config.get("config", {})},
            )
        return self._llm_instances[index]

    def _get_embedder(self, index: int) -> Any:
        """Lazy-create an embedder instance for the given provider index."""
        if index not in self._embedder_instances:
            from engram.utils.factory import EmbedderFactory
            config = self._chain[index]
            self._embedder_instances[index] = EmbedderFactory.create(
                provider=config.get("embedder_provider", config["provider"]),
                config={"model": config.get("embedder_model", config.get("model", "")),
                         **config.get("config", {})},
            )
        return self._embedder_instances[index]

    def generate(self, prompt: str) -> str:
        """Try each provider in order until one succeeds."""
        last_error = None

        for i in range(len(self._chain)):
            index = (self._current_index + i) % len(self._chain)
            try:
                llm = self._get_llm(index)
                result = llm.generate(prompt)
                # Success — update current index
                if index != self._current_index:
                    self._fallback_count += 1
                    logger.info(
                        "Fallback: %s → %s (attempt %d)",
                        self._chain[self._current_index].get("provider"),
                        self._chain[index].get("provider"),
                        i + 1,
                    )
                self._current_index = index
                return result
            except Exception as e:
                last_error = e
                self._error_history.append({
                    "provider": self._chain[index].get("provider"),
                    "model": self._chain[index].get("model"),
                    "error": str(e),
                    "timestamp": time.time(),
                })
                logger.warning(
                    "Provider %s failed: %s",
                    self._chain[index].get("provider"), e,
                )

        raise RuntimeError(
            f"All {len(self._chain)} providers failed. Last error: {last_error}"
        )

    def embed(self, text: str) -> list[float]:
        """Try each embedder in order."""
        last_error = None

        for i in range(len(self._chain)):
            index = (self._current_index + i) % len(self._chain)
            try:
                embedder = self._get_embedder(index)
                result = embedder.embed(text)
                return result
            except Exception as e:
                last_error = e
                self._error_history.append({
                    "provider": self._chain[index].get("provider"),
                    "type": "embedding",
                    "error": str(e),
                    "timestamp": time.time(),
                })
                logger.warning(
                    "Embedder %s failed: %s",
                    self._chain[index].get("provider"), e,
                )

        raise RuntimeError(
            f"All {len(self._chain)} embedders failed. Last error: {last_error}"
        )

    def status(self) -> dict:
        """Current provider, fallback count, error history."""
        current = self._chain[self._current_index] if self._chain else {}
        return {
            "current_provider": current.get("provider", ""),
            "current_model": current.get("model", ""),
            "current_index": self._current_index,
            "total_providers": len(self._chain),
            "fallback_count": self._fallback_count,
            "recent_errors": self._error_history[-10:],
        }

    def reset(self) -> None:
        """Reset to primary provider."""
        self._current_index = 0
        logger.info("Fallback chain reset to primary provider")
