import logging
import os
from typing import Optional

from engram.llms.base import BaseLLM

logger = logging.getLogger(__name__)


class NvidiaLLM(BaseLLM):
    """LLM provider for NVIDIA API (OpenAI-compatible). Default model: Llama 3.1 8B Instruct."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ImportError("openai package is required for NvidiaLLM") from exc

        api_key = (
            self.config.get("api_key")
            or os.getenv("LLAMA_API_KEY")
            or os.getenv("NVIDIA_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "NVIDIA API key required. Set config['api_key'], "
                "LLAMA_API_KEY, or NVIDIA_API_KEY env var."
            )

        base_url = self.config.get("base_url", "https://integrate.api.nvidia.com/v1")
        timeout = self.config.get("timeout", 60)
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = self.config.get("model", "meta/llama-3.1-8b-instruct")
        self.temperature = self.config.get("temperature", 0.2)
        self.max_tokens = self.config.get("max_tokens", 1024)
        self.top_p = self.config.get("top_p", 0.7)
        self.enable_thinking = self.config.get("enable_thinking", False)

    def generate(self, prompt: str) -> str:
        try:
            extra_kwargs = {}
            if self.enable_thinking:
                extra_kwargs["extra_body"] = {
                    "chat_template_kwargs": {"thinking": True}
                }

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
                stream=False,
                **extra_kwargs,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.error("NVIDIA LLM generate failed (model=%s): %s", self.model, exc)
            raise RuntimeError(
                f"NVIDIA LLM generation failed (model={self.model}): {exc}"
            ) from exc
