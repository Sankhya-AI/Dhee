import logging
import os
import time
from typing import Optional

from dhee.llms.base import BaseLLM

logger = logging.getLogger(__name__)


class NvidiaLLM(BaseLLM):
    """LLM provider for NVIDIA API (OpenAI-compatible). Default model: Qwen 3.5 397B."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ImportError("openai package is required for NvidiaLLM") from exc

        api_key = (
            self.config.get("api_key")
            or os.getenv("NVIDIA_QWEN_API_KEY")
            or os.getenv("NVIDIA_LLAMA_4_MAV_API_KEY")
            or os.getenv("LLAMA_API_KEY")
            or os.getenv("NVIDIA_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "NVIDIA API key required. Set config['api_key'], "
                "NVIDIA_QWEN_API_KEY, NVIDIA_LLAMA_4_MAV_API_KEY, LLAMA_API_KEY, or NVIDIA_API_KEY env var."
            )

        base_url = self.config.get("base_url", "https://integrate.api.nvidia.com/v1")
        timeout = self.config.get("timeout", 120)
        max_retries = self.config.get("max_retries")
        client_kwargs = {
            "base_url": base_url,
            "api_key": api_key,
            "timeout": timeout,
        }
        if max_retries is not None:
            client_kwargs["max_retries"] = int(max_retries)
        self.client = OpenAI(**client_kwargs)
        self.model = self.config.get("model", "openai/gpt-oss-120b")
        self.temperature = self.config.get("temperature", 0.2)
        self.max_tokens = self.config.get("max_tokens", 4096)
        self.top_p = self.config.get("top_p", 0.7)
        self.enable_thinking = self.config.get("enable_thinking", False)

    def generate(self, prompt: str) -> str:
        from openai import APITimeoutError, APIConnectionError

        max_app_retries = self.config.get("app_retries", 3)
        backoff_base = 10

        for attempt in range(1, max_app_retries + 1):
            try:
                extra_kwargs = {}
                if self.enable_thinking:
                    extra_kwargs["extra_body"] = {
                        "chat_template_kwargs": {"enable_thinking": True}
                    }
                elif "gemma" in self.model.lower():
                    extra_kwargs["extra_body"] = {
                        "chat_template_kwargs": {"enable_thinking": False}
                    }

                use_stream = self.enable_thinking or self.config.get("stream", False)

                create_kwargs = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": self.temperature,
                    "top_p": self.top_p,
                    "max_tokens": self.max_tokens,
                    "stream": use_stream,
                    **extra_kwargs,
                }
                if use_stream:
                    create_kwargs["stream_options"] = {"include_usage": True}
                response = self.client.chat.completions.create(**create_kwargs)

                if use_stream:
                    reasoning_parts: list[str] = []
                    content_parts: list[str] = []
                    stream_usage = None
                    for chunk in response:
                        # Capture usage from final chunk if available
                        if hasattr(chunk, "usage") and chunk.usage:
                            stream_usage = chunk.usage
                        if not getattr(chunk, "choices", None):
                            continue
                        delta = chunk.choices[0].delta
                        reasoning_piece = getattr(delta, "reasoning_content", None)
                        if reasoning_piece:
                            reasoning_parts.append(str(reasoning_piece))
                        content_piece = getattr(delta, "content", None)
                        if content_piece:
                            content_parts.append(str(content_piece))
                    content = "".join(content_parts).strip()
                    if not content and reasoning_parts:
                        content = "".join(reasoning_parts).strip()
                    # Track tokens
                    if stream_usage:
                        in_tok = getattr(stream_usage, "prompt_tokens", 0) or 0
                        out_tok = getattr(stream_usage, "completion_tokens", 0) or 0
                    else:
                        in_tok = len(prompt) // 4  # estimate
                        out_tok = len(content) // 4
                    self.usage.record(in_tok, out_tok, self._current_purpose)
                    return content
                else:
                    result = response.choices[0].message.content or ""
                    # Track tokens from response usage
                    if hasattr(response, "usage") and response.usage:
                        in_tok = response.usage.prompt_tokens or 0
                        out_tok = response.usage.completion_tokens or 0
                    else:
                        in_tok = len(prompt) // 4
                        out_tok = len(result) // 4
                    self.usage.record(in_tok, out_tok, self._current_purpose)
                    return result
            except (APITimeoutError, APIConnectionError) as exc:
                if attempt < max_app_retries:
                    wait = backoff_base * attempt
                    logger.warning(
                        "NVIDIA LLM timeout/connection error (model=%s, attempt %d/%d), retrying in %ds: %s",
                        self.model, attempt, max_app_retries, wait, exc,
                    )
                    time.sleep(wait)
                    continue
                logger.error("NVIDIA LLM generate failed after %d attempts (model=%s): %s", max_app_retries, self.model, exc)
                raise RuntimeError(
                    f"NVIDIA LLM generation failed after {max_app_retries} attempts (model={self.model}): {exc}"
                ) from exc
            except Exception as exc:
                logger.error("NVIDIA LLM generate failed (model=%s): %s", self.model, exc)
                raise RuntimeError(
                    f"NVIDIA LLM generation failed (model={self.model}): {exc}"
                ) from exc
