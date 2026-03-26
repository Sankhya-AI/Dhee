from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TokenUsage:
    """Tracks token usage across all LLM calls."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_calls: int = 0
    calls_by_purpose: dict = field(default_factory=dict)  # purpose → {input, output, count}

    def record(self, input_tokens: int, output_tokens: int, purpose: str = "unknown"):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_calls += 1
        if purpose not in self.calls_by_purpose:
            self.calls_by_purpose[purpose] = {"input": 0, "output": 0, "count": 0}
        self.calls_by_purpose[purpose]["input"] += input_tokens
        self.calls_by_purpose[purpose]["output"] += output_tokens
        self.calls_by_purpose[purpose]["count"] += 1

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def to_dict(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "total_calls": self.total_calls,
            "calls_by_purpose": dict(self.calls_by_purpose),
        }

    def reset(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0
        self.calls_by_purpose.clear()


class BaseLLM(ABC):
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.usage = TokenUsage()
        self._current_purpose = "unknown"

    @abstractmethod
    def generate(self, prompt: str) -> str:
        pass

    def set_purpose(self, purpose: str):
        """Set the current purpose for cost tracking (e.g., 'extraction', 'answer', 'enrichment')."""
        self._current_purpose = purpose
