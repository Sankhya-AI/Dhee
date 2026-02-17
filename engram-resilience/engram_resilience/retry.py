"""SmartRetry — exponential backoff with jitter."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class SmartRetry:
    """Execute operations with exponential backoff and optional jitter.

    Provides retry logic with configurable delays and a fallback mechanism.
    """

    def __init__(self, max_retries: int = 3, base_delay: float = 1.0,
                 max_delay: float = 60.0, jitter: bool = True) -> None:
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._jitter = jitter

    def _compute_delay(self, attempt: int) -> float:
        """Compute delay for the given attempt number (0-indexed)."""
        delay = self._base_delay * (2 ** attempt)
        delay = min(delay, self._max_delay)
        if self._jitter:
            delay = delay * (0.5 + random.random() * 0.5)
        return delay

    def execute(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute with exponential backoff. Raises after max_retries."""
        last_error = None

        for attempt in range(self._max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self._max_retries:
                    delay = self._compute_delay(attempt)
                    logger.warning(
                        "Retry %d/%d after %.1fs: %s",
                        attempt + 1, self._max_retries, delay, e,
                    )
                    time.sleep(delay)

        raise last_error  # type: ignore[misc]

    def with_fallback(self, fn: Callable, fallback_fn: Callable,
                      *args: Any, **kwargs: Any) -> Any:
        """Try fn with retries, fall back to fallback_fn on total failure."""
        try:
            return self.execute(fn, *args, **kwargs)
        except Exception as e:
            logger.warning("Primary failed after retries, using fallback: %s", e)
            return fallback_fn(*args, **kwargs)
