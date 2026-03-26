"""
Consolidation Engine — promotes important active signals to passive memory.

Mirrors how the brain consolidates short-term memory into long-term during rest:
- Directives are always promoted (permanent rules)
- Critical-tier signals are promoted (high importance)
- High-read signals are promoted (frequently accessed = important)
"""

import logging
from typing import Any, Dict, TYPE_CHECKING

from dhee.configs.active import ActiveMemoryConfig
from dhee.core.active_memory import ActiveMemoryStore

if TYPE_CHECKING:
    from dhee.memory.main import Memory

logger = logging.getLogger(__name__)


class ConsolidationEngine:
    """Promotes qualifying active signals into passive (Engram) memory."""

    def __init__(
        self,
        active_store: ActiveMemoryStore,
        memory: "Memory",
        config: ActiveMemoryConfig,
    ):
        self.active = active_store
        self.memory = memory
        self.config = config
        self.consolidation = config.consolidation

    def run_cycle(self) -> Dict[str, Any]:
        """Run one consolidation cycle. Returns promotion stats."""
        candidates = self.active.get_consolidation_candidates(
            min_age_seconds=self.config.consolidation_min_age_seconds,
            min_reads=self.config.consolidation_min_reads,
        )

        promoted = []
        skipped = 0
        errors = 0

        for signal in candidates:
            if not self._should_promote(signal):
                skipped += 1
                continue
            try:
                self._promote_to_passive(signal)
                promoted.append(signal["id"])
            except Exception:
                logger.exception("Failed to promote signal %s", signal["id"])
                errors += 1

        if promoted:
            self.active.mark_consolidated(promoted)

        return {
            "promoted": len(promoted),
            "checked": len(candidates),
            "skipped": skipped,
            "errors": errors,
        }

    def _should_promote(self, signal: Dict[str, Any]) -> bool:
        """Determine if a signal qualifies for promotion to passive memory."""
        signal_type = signal.get("signal_type", "")
        ttl_tier = signal.get("ttl_tier", "")
        read_count = signal.get("read_count", 0)

        # Directives always promote
        if signal_type == "directive" and self.consolidation.directive_to_passive:
            return True

        # Critical tier promotes
        if ttl_tier == "critical" and self.consolidation.promote_critical:
            return True

        # High-read signals promote
        if (
            self.consolidation.promote_high_read
            and read_count >= self.consolidation.promote_read_threshold
        ):
            return True

        return False

    def _promote_to_passive(self, signal: Dict[str, Any]) -> None:
        """Add a signal's content to passive memory via Memory.add()."""
        signal_type = signal.get("signal_type", "event")
        user_id = signal.get("user_id", "default")
        key = signal.get("key", "")
        value = signal.get("value", "")

        # Build content string
        content = f"[{key}] {value}" if key else value

        self.memory.add(
            messages=content,
            user_id=user_id,
            metadata={
                "source": "active_signal",
                "signal_key": key,
                "signal_type": signal_type,
            },
            immutable=(signal_type == "directive"),
            initial_layer="lml" if signal_type == "directive" else "sml",
            infer=False,
        )
