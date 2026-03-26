"""Observability — structured logging and metrics for Dhee.

Counters, histograms, and a @measure decorator for all critical paths.
Prometheus-compatible /metrics endpoint output.
"""

import logging
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class Counter:
    """Thread-safe monotonic counter."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, amount: int = 1) -> None:
        with self._lock:
            self._value += amount

    @property
    def value(self) -> int:
        return self._value

    def to_prometheus(self) -> str:
        return (
            f"# HELP {self.name} {self.description}\n"
            f"# TYPE {self.name} counter\n"
            f"{self.name} {self._value}\n"
        )


class Histogram:
    """Thread-safe latency histogram with percentile tracking."""

    def __init__(self, name: str, description: str = "", max_samples: int = 1000):
        self.name = name
        self.description = description
        self._samples: List[float] = []
        self._max_samples = max_samples
        self._lock = threading.Lock()
        self._sum = 0.0
        self._count = 0

    def observe(self, value: float) -> None:
        with self._lock:
            self._samples.append(value)
            self._sum += value
            self._count += 1
            if len(self._samples) > self._max_samples:
                self._samples = self._samples[-self._max_samples:]

    @property
    def count(self) -> int:
        return self._count

    @property
    def avg(self) -> float:
        return self._sum / self._count if self._count else 0.0

    def percentile(self, p: float) -> float:
        with self._lock:
            if not self._samples:
                return 0.0
            sorted_samples = sorted(self._samples)
            idx = int(len(sorted_samples) * p / 100)
            return sorted_samples[min(idx, len(sorted_samples) - 1)]

    def to_prometheus(self) -> str:
        p50 = self.percentile(50)
        p95 = self.percentile(95)
        p99 = self.percentile(99)
        return (
            f"# HELP {self.name} {self.description}\n"
            f"# TYPE {self.name} histogram\n"
            f'{self.name}{{quantile="0.5"}} {p50:.4f}\n'
            f'{self.name}{{quantile="0.95"}} {p95:.4f}\n'
            f'{self.name}{{quantile="0.99"}} {p99:.4f}\n'
            f"{self.name}_sum {self._sum:.4f}\n"
            f"{self.name}_count {self._count}\n"
        )


class DheeMetrics:
    """Centralized metrics registry for Dhee."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Counters
        self.memory_add = Counter("dhee_memory_add_total", "Total memories added")
        self.memory_search = Counter("dhee_memory_search_total", "Total memory searches")
        self.deterministic_resolve = Counter(
            "dhee_deterministic_resolve_total", "Queries resolved deterministically via SQL"
        )
        self.vector_search = Counter("dhee_vector_search_total", "Vector search operations")
        self.cognitive_loop = Counter("dhee_cognitive_loop_total", "Cognitive decomposition runs")
        self.llm_calls = Counter("dhee_llm_calls_total", "Total LLM API calls")
        self.engram_extractions = Counter(
            "dhee_engram_extractions_total", "Structured engram extractions"
        )
        self.prospective_triggered = Counter(
            "dhee_prospective_triggered_total", "Prospective scenes triggered"
        )
        self.facts_stored = Counter("dhee_facts_stored_total", "Structured facts stored")
        self.rerank_calls = Counter("dhee_rerank_calls_total", "Reranker invocations")

        # Histograms
        self.add_latency = Histogram(
            "dhee_memory_add_seconds", "Memory add latency in seconds"
        )
        self.search_latency = Histogram(
            "dhee_memory_search_seconds", "Memory search latency in seconds"
        )
        self.resolve_latency = Histogram(
            "dhee_deterministic_resolve_seconds", "Deterministic resolution latency"
        )
        self.extraction_latency = Histogram(
            "dhee_engram_extraction_seconds", "Engram extraction latency"
        )
        self.cognitive_latency = Histogram(
            "dhee_cognitive_loop_seconds", "Cognitive loop latency"
        )
        self.llm_latency = Histogram("dhee_llm_call_seconds", "LLM call latency")
        self.rerank_latency = Histogram("dhee_rerank_seconds", "Reranker latency")

    def to_prometheus(self) -> str:
        """Render all metrics in Prometheus text format."""
        parts = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if isinstance(attr, (Counter, Histogram)):
                parts.append(attr.to_prometheus())
        return "\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Render metrics as a dict for JSON API."""
        result = {}
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if isinstance(attr, Counter):
                result[attr.name] = attr.value
            elif isinstance(attr, Histogram):
                result[attr.name] = {
                    "count": attr.count,
                    "avg": round(attr.avg, 4),
                    "p50": round(attr.percentile(50), 4),
                    "p95": round(attr.percentile(95), 4),
                    "p99": round(attr.percentile(99), 4),
                }
        return result


def get_metrics() -> DheeMetrics:
    """Get the singleton metrics instance."""
    return DheeMetrics()


def measure(counter_name: str = "", histogram_name: str = ""):
    """Decorator to measure function execution.

    Usage:
        @measure("memory_add", "add_latency")
        def add(self, ...): ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            metrics = get_metrics()
            t0 = time.monotonic()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = time.monotonic() - t0
                if counter_name:
                    counter = getattr(metrics, counter_name, None)
                    if counter:
                        counter.inc()
                if histogram_name:
                    histogram = getattr(metrics, histogram_name, None)
                    if histogram:
                        histogram.observe(elapsed)
        return wrapper
    return decorator
