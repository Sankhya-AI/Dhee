"""HeartbeatRunner — background thread that ticks scheduled behaviors."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engram_heartbeat.heartbeat import Heartbeat

logger = logging.getLogger(__name__)


class HeartbeatRunner:
    """Background thread that periodically checks and runs due behaviors."""

    def __init__(self, heartbeat: Heartbeat, tick_interval: float = 60.0) -> None:
        self._heartbeat = heartbeat
        self._tick_interval = tick_interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the background tick loop."""
        if self._running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="heartbeat-runner")
        self._thread.start()
        self._running = True
        logger.info("HeartbeatRunner started (interval=%.1fs)", self._tick_interval)

    def stop(self) -> None:
        """Stop the background tick loop."""
        if not self._running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._tick_interval + 1)
            self._thread = None
        self._running = False
        logger.info("HeartbeatRunner stopped")

    def _loop(self) -> None:
        """Main tick loop."""
        while not self._stop_event.is_set():
            try:
                self._heartbeat.tick()
            except Exception as e:
                logger.error("HeartbeatRunner tick error: %s", e)
            self._stop_event.wait(self._tick_interval)
