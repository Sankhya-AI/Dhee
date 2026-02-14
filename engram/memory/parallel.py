"""ParallelExecutor — ThreadPoolExecutor wrapper for parallel I/O calls.

Used internally by the Memory class to parallelize independent LLM and
embedding calls without requiring an async rewrite. The public API stays
synchronous.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, List, Tuple

logger = logging.getLogger(__name__)


class ParallelExecutor:
    """Thread-pool executor for parallelizing I/O-bound calls (LLM, embedder).

    Thread-safe: only I/O calls are parallelized. DB writes and state
    mutations remain on the calling thread.
    """

    def __init__(self, max_workers: int = 4):
        self._max_workers = max_workers
        self._pool: ThreadPoolExecutor | None = None

    def _ensure_pool(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self._max_workers)
        return self._pool

    def run_parallel(
        self, tasks: List[Tuple[Callable[..., Any], tuple]]
    ) -> List[Any]:
        """Run *tasks* in parallel and return results in order.

        Each task is a ``(callable, args_tuple)`` pair. If any task raises,
        its exception is propagated to the caller.
        """
        if not tasks:
            return []
        if len(tasks) == 1:
            fn, args = tasks[0]
            return [fn(*args)]

        pool = self._ensure_pool()
        futures: List[Future[Any]] = [
            pool.submit(fn, *args) for fn, args in tasks
        ]
        return [f.result() for f in futures]

    def shutdown(self) -> None:
        """Shut down the thread pool (non-blocking)."""
        if self._pool is not None:
            try:
                self._pool.shutdown(wait=False)
            except Exception:
                pass
            self._pool = None
