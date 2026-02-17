"""Pure functions for working memory buffer operations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class WMItem:
    """A single item in working memory."""
    content: str
    tag: str = ""
    activation: float = 1.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "content": self.content,
            "tag": self.tag,
            "activation": round(self.activation, 4),
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
            "metadata": self.metadata,
        }


def compute_activation_decay(
    item: WMItem,
    now: datetime,
    half_life_minutes: float,
) -> float:
    """Compute decayed activation for an item.

    Uses exponential decay with configurable half-life.
    """
    elapsed_minutes = (now - item.last_accessed).total_seconds() / 60.0
    if elapsed_minutes <= 0:
        return item.activation
    decay_rate = math.log(2) / max(1.0, half_life_minutes)
    return item.activation * math.exp(-decay_rate * elapsed_minutes)


def find_eviction_candidate(items: Dict[str, WMItem]) -> Optional[str]:
    """Find the item with lowest activation for eviction."""
    if not items:
        return None
    return min(items, key=lambda k: items[k].activation)


def is_relevant_to_query(item: WMItem, query: str) -> bool:
    """Simple relevance check: word overlap between item and query."""
    if not query:
        return False
    query_words = set(query.lower().split())
    content_words = set(item.content.lower().split())
    tag_words = set(item.tag.lower().split()) if item.tag else set()
    all_words = content_words | tag_words
    overlap = query_words & all_words
    meaningful = {w for w in overlap if len(w) > 3}
    return len(meaningful) >= 1
