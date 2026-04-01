"""
Consolidation Engine — promotes important active signals to passive memory.

v3 FIX: Breaks the feedback loop identified in the architecture critique.

Old behavior (DANGEROUS):
    _promote_to_passive() called memory.add() → triggered full enrichment
    pipeline → could create new active signals → infinite consolidation loop.

New behavior (SAFE):
    _promote_to_passive() calls memory.add() with infer=False AND tags
    promoted memories with source="consolidated" provenance metadata.
    _should_promote() rejects signals that were already consolidated
    (prevents re-consolidation of promoted content).

The enrichment pipeline is explicitly skipped for consolidated memories
because the content was already enriched when it entered active memory.
"""

import logging
from typing import Any, Dict, TYPE_CHECKING

from dhee.configs.active import ActiveMemoryConfig
from dhee.core.active_memory import ActiveMemoryStore

if TYPE_CHECKING:
    from dhee.memory.main import FullMemory

logger = logging.getLogger(__name__)


class ConsolidationEngine:
    """Promotes qualifying active signals into passive (Engram) memory."""

    def __init__(
        self,
        active_store: ActiveMemoryStore,
        memory: "FullMemory",
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
        feedback_loop_blocked = 0

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
            "feedback_loop_blocked": feedback_loop_blocked,
        }

    def _should_promote(self, signal: Dict[str, Any]) -> bool:
        """Determine if a signal qualifies for promotion to passive memory."""
        signal_type = signal.get("signal_type", "")
        ttl_tier = signal.get("ttl_tier", "")
        read_count = signal.get("read_count", 0)

        # v3 FIX: Block re-consolidation of already-consolidated content.
        # This breaks the feedback loop where promoted content generates
        # new active signals that get re-consolidated infinitely.
        signal_metadata = signal.get("metadata", {})
        if isinstance(signal_metadata, dict):
            if signal_metadata.get("source") == "consolidated":
                return False
            if signal_metadata.get("consolidated_from"):
                return False

        # Also check the value field for consolidation markers
        value = signal.get("value", "")
        if isinstance(value, str) and "[consolidated]" in value.lower():
            return False

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
        """Add a signal's content to passive memory.

        v3 FIX: Uses infer=False to skip the LLM enrichment pipeline.
        Tags with source="consolidated" to prevent re-consolidation.
        """
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
                # Provenance: identifies this as consolidated content
                "source": "consolidated",
                "consolidated_from": signal.get("id"),
                "signal_key": key,
                "signal_type": signal_type,
            },
            immutable=(signal_type == "directive"),
            initial_layer="lml" if signal_type == "directive" else "sml",
            # v3 FIX: Skip enrichment pipeline entirely.
            # Content was already enriched when it entered active memory.
            # Re-enrichment would generate divergent facts/entities.
            infer=False,
        )
