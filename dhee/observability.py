"""Engram Observability — compatibility-safe no-op implementation.

Core Engram does not require metrics infrastructure at runtime, but enterprise
and API layers import symbols from this module. Keep this interface stable and
side-effect free so those imports always succeed.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator

logger = logging.getLogger("dhee")


class _NoOpMetrics:
    """Drop-in replacement that silently discards all metric calls."""

    def __getattr__(self, _: str):
        def _noop(*args, **kwargs):
            return None

        return _noop

    @contextmanager
    def measure(self, *args, **kwargs) -> Iterator[None]:
        yield

    def get_summary(self) -> Dict[str, Any]:
        return {}


metrics = _NoOpMetrics()


def add_metrics_routes(app: Any) -> None:
    """Register a lightweight /metrics endpoint if FastAPI is available."""
    try:
        routes = getattr(app, "routes", [])
        if any(getattr(route, "path", None) == "/metrics" for route in routes):
            return

        @app.get("/metrics")
        async def _metrics_endpoint() -> Dict[str, Any]:
            return metrics.get_summary()
    except Exception:
        # Keep observability strictly non-blocking.
        return
