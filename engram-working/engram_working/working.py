"""WorkingMemory — volatile short-term buffer with capacity limits."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engram_working.buffer import (
    WMItem,
    compute_activation_decay,
    find_eviction_candidate,
    is_relevant_to_query,
)
from engram_working.config import WorkingMemoryConfig

logger = logging.getLogger(__name__)


class WorkingMemory:
    """Working memory — The Blackboard.

    Volatile short-term buffer. Primary store is an in-process dict,
    NOT the database. Items decay in minutes; overflow pushes least-active
    items to long-term memory.

    Provides:
    - Push items with tags and metadata
    - Peek at items (refreshes activation)
    - Pop items out of working memory
    - Automatic eviction on overflow
    - Flush to long-term memory
    """

    def __init__(
        self,
        memory: Any,
        user_id: str = "default",
        capacity: int = 7,
        config: Optional[WorkingMemoryConfig] = None,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self.config = config or WorkingMemoryConfig(capacity=capacity)
        self._items: Dict[str, WMItem] = {}

    # ── Internal ──

    def _apply_decay(self) -> None:
        """Apply activation decay to all items, evict dead ones."""
        now = datetime.now(timezone.utc)
        to_evict = []
        for key, item in self._items.items():
            item.activation = compute_activation_decay(
                item, now, self.config.decay_minutes
            )
            if item.activation < self.config.min_activation:
                to_evict.append(key)

        for key in to_evict:
            self._evict(key)

    def _evict(self, key: str) -> None:
        """Evict an item, optionally flushing to long-term memory."""
        item = self._items.pop(key, None)
        if item and self.config.auto_flush_to_longterm:
            self._flush_item(item)

    def _flush_item(self, item: WMItem) -> None:
        """Flush a single item to long-term memory."""
        try:
            metadata = {
                **item.metadata,
                "memory_type": "working_memory_flush",
                "wm_tag": item.tag,
                "wm_activation_at_flush": round(item.activation, 4),
                "wm_access_count": item.access_count,
                "wm_flushed_at": datetime.now(timezone.utc).isoformat(),
            }
            self.memory.add(
                item.content,
                user_id=self.user_id,
                metadata=metadata,
                infer=False,
            )
        except Exception as e:
            logger.warning("Failed to flush WM item to long-term memory: %s", e)

    def _ensure_capacity(self) -> Optional[Dict]:
        """Evict least-active item if at capacity. Returns evicted item info."""
        if len(self._items) < self.config.capacity:
            return None

        candidate = find_eviction_candidate(self._items)
        if candidate:
            item = self._items[candidate]
            evicted_info = {"key": candidate, **item.to_dict()}
            self._evict(candidate)
            return evicted_info
        return None

    # ── Public API ──

    def push(
        self,
        content: str,
        tag: str = "",
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Push an item into working memory. Evicts least-active if at capacity."""
        self._apply_decay()
        evicted = self._ensure_capacity()

        key = str(uuid.uuid4())[:8]
        item = WMItem(
            content=content,
            tag=tag,
            activation=1.0,
            metadata=metadata or {},
        )
        self._items[key] = item

        result = {"key": key, **item.to_dict()}
        if evicted:
            result["evicted"] = evicted
        result["buffer_size"] = len(self._items)
        return result

    def peek(self, key: str) -> Optional[Dict]:
        """Peek at an item. Refreshes its activation."""
        self._apply_decay()
        item = self._items.get(key)
        if not item:
            return None

        # Refresh activation
        item.activation = min(1.0, item.activation + 0.3)
        item.last_accessed = datetime.now(timezone.utc)
        item.access_count += 1

        return {"key": key, **item.to_dict()}

    def refresh(self, key: str) -> Optional[Dict]:
        """Explicitly refresh an item's activation to maximum."""
        item = self._items.get(key)
        if not item:
            return None

        item.activation = 1.0
        item.last_accessed = datetime.now(timezone.utc)
        item.access_count += 1

        return {"key": key, **item.to_dict()}

    def pop(self, key: str) -> Optional[Dict]:
        """Remove an item from working memory (does NOT flush to long-term)."""
        item = self._items.pop(key, None)
        if not item:
            return None
        return {"key": key, **item.to_dict()}

    def list(self) -> List[Dict]:
        """List all items in working memory, sorted by activation."""
        self._apply_decay()
        items = [
            {"key": k, **v.to_dict()}
            for k, v in self._items.items()
        ]
        items.sort(key=lambda x: x["activation"], reverse=True)
        return items

    def flush_to_longterm(self) -> Dict:
        """Flush all working memory items to long-term memory and clear buffer."""
        count = len(self._items)
        for item in self._items.values():
            self._flush_item(item)
        self._items.clear()
        return {"flushed": count, "buffer_size": 0}

    def get_relevant(self, query: str) -> List[Dict]:
        """Get working memory items relevant to a query."""
        self._apply_decay()
        relevant = []
        for key, item in self._items.items():
            if is_relevant_to_query(item, query):
                relevant.append({"key": key, **item.to_dict()})
        relevant.sort(key=lambda x: x["activation"], reverse=True)
        return relevant

    @property
    def size(self) -> int:
        return len(self._items)
