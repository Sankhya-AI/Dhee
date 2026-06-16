import logging
from typing import Optional

from dhee.llms.base import BaseLLM

logger = logging.getLogger(__name__)


class OpenAILLM(BaseLLM):
    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ImportError("openai package is required for OpenAILLM") from exc
        timeout = self.config.get("timeout", 60)
        client_kwargs = {"timeout": timeout}
        if self.config.get("api_key"):
            client_kwargs["api_key"] = self.config["api_key"]
        if self.config.get("base_url"):
            client_kwargs["base_url"] = self.config["base_url"]
        self.client = OpenAI(**client_kwargs)
        self.model = self.config.get("model", "gpt-5.2")
        self.temperature = self.config.get("temperature", 0.1)
        self.max_tokens = self.config.get("max_tokens", 1000)

    def generate(self, prompt: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.error("OpenAI LLM generate failed (model=%s): %s", self.model, exc)
            raise RuntimeError(
                f"OpenAI LLM generation failed (model={self.model}): {exc}"
            ) from exc
