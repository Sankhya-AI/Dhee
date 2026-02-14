"""Reference-aware retention helpers."""

from __future__ import annotations

from typing import Any, Dict, Iterable


class RefCountManager:
    def __init__(self, db):
        self.db = db

    def record_retrieval_refs(
        self,
        memory_ids: Iterable[str],
        agent_id: str,
        strong: bool = False,
        ttl_hours: int = 24 * 14,
    ) -> None:
        ref_type = "strong" if strong else "weak"
        subscriber = f"agent:{agent_id}"
        for memory_id in memory_ids:
            if not memory_id:
                continue
            self.db.add_memory_subscriber(
                str(memory_id),
                subscriber,
                ref_type=ref_type,
                ttl_hours=None if strong else ttl_hours,
            )

    def get_refcount(self, memory_id: str) -> Dict[str, Any]:
        return self.db.get_memory_refcount(memory_id)

    def should_protect_from_forgetting(self, memory_id: str) -> bool:
        ref = self.get_refcount(memory_id)
        return int(ref.get("strong", 0)) > 0

    def weak_dampening_factor(self, memory_id: str) -> float:
        ref = self.get_refcount(memory_id)
        weak = int(ref.get("weak", 0))
        # More weak refs => slower forgetting.
        return 1.0 + min(weak, 10) * 0.15

    def cleanup_stale_refs(self) -> int:
        return int(self.db.cleanup_stale_memory_subscribers())
