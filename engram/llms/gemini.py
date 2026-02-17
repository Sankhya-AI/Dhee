import logging
import os
from typing import Optional

from google import genai

from engram.llms.base import BaseLLM

logger = logging.getLogger(__name__)


class GeminiLLM(BaseLLM):
    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.api_key = self.config.get("api_key") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API key not provided. Set GEMINI_API_KEY or pass api_key in config.")

        self.model = self.config.get("model", "gemini-2.0-flash")
        self.temperature = self.config.get("temperature", 0.1)
        self.max_tokens = self.config.get("max_tokens", 1024)
        self._client = genai.Client(api_key=self.api_key)

    def generate(self, prompt: str) -> str:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                },
            )
            return _extract_text_from_response(response)
        except RuntimeError:
            raise
        except Exception as exc:
            logger.error("Gemini LLM generate failed (model=%s): %s", self.model, exc)
            raise RuntimeError(
                f"Gemini LLM generation failed (model={self.model}): {exc}"
            ) from exc


def _extract_text_from_response(response) -> str:
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
