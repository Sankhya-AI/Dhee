"""In-process topic-based pub/sub. Thread-safe."""

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PubSub:
    """In-process topic-based publish/subscribe."""

    def __init__(self) -> None:
        self._subs: Dict[str, List[Tuple[Callable, Optional[str]]]] = {}
        self._lock = threading.RLock()

    def subscribe(
        self,
        topic: str,
        callback: Callable,
        agent_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            if topic not in self._subs:
                self._subs[topic] = []
            self._subs[topic].append((callback, agent_id))

    def unsubscribe(self, topic: str, callback: Callable) -> None:
        with self._lock:
            if topic not in self._subs:
                return
            self._subs[topic] = [
                (cb, aid) for cb, aid in self._subs[topic] if cb is not callback
            ]

    def publish(
        self,
        topic: str,
        data: Any,
        agent_id: Optional[str] = None,
    ) -> int:
        with self._lock:
            subs = list(self._subs.get(topic, []))
        count = 0
        for cb, _ in subs:
            try:
                cb(topic, data, agent_id)
                count += 1
            except Exception:
                logger.exception("Error in subscriber callback for topic %s", topic)
        return count

    def subscribers(self, topic: str) -> int:
        with self._lock:
            return len(self._subs.get(topic, []))
