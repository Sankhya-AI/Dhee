"""आलय (Alaya) — Storehouse consciousness with seed activation.

Buddhist Yogacara: Alaya-vijnana is the storehouse consciousness.
All experiences are stored as bija (seeds). Seeds have two states:
  - Dormant: stored but never activated
  - Ripened (vipaka): activated through retrieval, strengthened

Seeds ripen through three mechanisms:
  1. Pratyaya (conditions) — retrieved when relevant query arises
  2. Sahabhu (co-arising) — activated alongside related seeds
  3. Vipaka (fruition) — contributed to a correct answer

Seeds that never ripen despite relevant queries → re-extraction candidates.
Seeds that consistently ripen together → associative strengthening.

This module works WITH the existing FadeMem/trace system:
  - FadeMem handles time-based decay (forgetting curve)
  - Traces handle multi-timescale consolidation (fast→mid→slow)
  - Alaya handles USE-based activation (which memories are actually useful)

Three different forces on memory strength:
  - Decay pulls strength DOWN over time (FadeMem)
  - Access resets the decay clock (existing record_access)
  - Activation pushes strength UP when memory proves useful (Alaya)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from dhee.core.samskara import SamskaraCollector

logger = logging.getLogger(__name__)


@dataclass
class BijaState:
    """State of a single seed (memory) in the storehouse.

    Tracks activation patterns beyond simple access_count.
    access_count = how many times it was retrieved
    activation_count = how many times it was USEFUL (contributed to answer)
    """

    memory_id: str
    activation_count: int = 0       # times this seed ripened (was useful)
    dormant_queries: int = 0        # times a relevant query missed this seed
    last_activated: float = 0.0     # timestamp of last successful activation
    co_activated_with: Dict[str, int] = field(default_factory=dict)  # memory_id → count
    strength_gradient: float = 0.0  # positive = strengthening, negative = weakening

    @property
    def is_dormant(self) -> bool:
        """A seed that has never activated after sufficient opportunities."""
        return self.activation_count == 0 and self.dormant_queries >= 3

    @property
    def ripening_ratio(self) -> float:
        """How often this seed ripens when it could.
        High ratio = consistently useful memory.
        Low ratio = retrieved but rarely helpful.
        """
        total = self.activation_count + self.dormant_queries
        if total == 0:
            return 0.0
        return self.activation_count / total

    @property
    def needs_re_extraction(self) -> bool:
        """Dormant seeds with many missed queries need re-extraction.
        The content is there but the structure isn't surfacing it.
        """
        return self.dormant_queries >= 5 and self.activation_count == 0


class AlayaStore:
    """Storehouse consciousness — tracks seed activation patterns.

    Sits between retrieval and answer synthesis:
    1. After retrieval: records which seeds were surfaced (pratyaya)
    2. After answer: records which seeds actually contributed (vipaka)
    3. Periodically: identifies dormant seeds and co-activation patterns

    Does NOT modify the database directly — produces signals for
    the memory pipeline to act on.
    """

    def __init__(
        self,
        samskara_collector: Optional[SamskaraCollector] = None,
        co_activation_threshold: int = 3,  # times before link is strong
        dormancy_threshold: int = 5,       # queries before seed is dormant
    ):
        self.samskara = samskara_collector
        self.co_activation_threshold = co_activation_threshold
        self.dormancy_threshold = dormancy_threshold

        # In-memory bija tracking (loaded from / flushed to DB)
        self._bija: Dict[str, BijaState] = {}

        # Session tracking for co-activation
        self._current_retrieval_set: List[str] = []

    def _get_bija(self, memory_id: str) -> BijaState:
        """Get or create bija state for a memory."""
        if memory_id not in self._bija:
            self._bija[memory_id] = BijaState(memory_id=memory_id)
        return self._bija[memory_id]

    # ------------------------------------------------------------------
    # Event handlers (called from memory pipeline)
    # ------------------------------------------------------------------

    def on_retrieval(
        self,
        query: str,
        retrieved_ids: List[str],
        user_id: str = "default",
    ) -> None:
        """Record that these seeds were surfaced by a query (pratyaya).

        Called after search() returns results.
        This is co-arising — all retrieved memories share context.
        """
        self._current_retrieval_set = list(retrieved_ids)

        # Record co-activation patterns
        for i, id_a in enumerate(retrieved_ids):
            bija_a = self._get_bija(id_a)
            for id_b in retrieved_ids[i + 1:]:
                # Bidirectional co-activation
                bija_a.co_activated_with[id_b] = (
                    bija_a.co_activated_with.get(id_b, 0) + 1
                )
                bija_b = self._get_bija(id_b)
                bija_b.co_activated_with[id_a] = (
                    bija_b.co_activated_with.get(id_a, 0) + 1
                )

    def on_activation(
        self,
        memory_ids: List[str],
        query: str = "",
        user_id: str = "default",
    ) -> None:
        """Record that these seeds actually contributed to an answer (vipaka).

        Called after answer synthesis when we know WHICH memories
        were grounded in the response. This is the ripening event.
        """
        now = time.time()
        for mid in memory_ids:
            bija = self._get_bija(mid)
            bija.activation_count += 1
            bija.last_activated = now
            # Positive gradient
            bija.strength_gradient = (
                0.9 * bija.strength_gradient + 0.1 * 1.0
            )

        # Mark non-activated retrieval results as dormant queries
        activated_set = set(memory_ids)
        for mid in self._current_retrieval_set:
            if mid not in activated_set:
                bija = self._get_bija(mid)
                bija.dormant_queries += 1
                # Negative gradient
                bija.strength_gradient = (
                    0.9 * bija.strength_gradient + 0.1 * (-0.3)
                )

        self._current_retrieval_set = []

    def on_retrieval_miss(
        self,
        query: str,
        expected_memory_ids: Optional[List[str]] = None,
        user_id: str = "default",
    ) -> None:
        """Record that a query should have found memories but didn't.

        Called when user corrects an answer by providing information
        that exists in the store but wasn't retrieved.
        """
        if expected_memory_ids:
            for mid in expected_memory_ids:
                bija = self._get_bija(mid)
                bija.dormant_queries += 1
                bija.strength_gradient = (
                    0.9 * bija.strength_gradient + 0.1 * (-0.5)
                )

    # ------------------------------------------------------------------
    # Analysis: what needs attention
    # ------------------------------------------------------------------

    def get_dormant_seeds(self) -> List[BijaState]:
        """Seeds that have never activated despite opportunities.

        These need re-extraction — the content exists but the
        structural representation isn't surfacing it.
        """
        return [
            bija for bija in self._bija.values()
            if bija.is_dormant
        ]

    def get_re_extraction_candidates(self) -> List[str]:
        """Memory IDs that need re-extraction via EngramExtractor.

        Returns IDs of memories where the structured representation
        (facts, context anchors, embeddings) needs to be regenerated.
        """
        return [
            bija.memory_id for bija in self._bija.values()
            if bija.needs_re_extraction
        ]

    def get_strong_associations(self) -> List[Tuple[str, str, int]]:
        """Memory pairs that consistently co-activate.

        Returns (id_a, id_b, count) tuples for pairs that
        co-activated above the threshold. These should be
        linked in engram_links as co_occurring.
        """
        seen: Set[Tuple[str, str]] = set()
        associations: List[Tuple[str, str, int]] = []

        for bija in self._bija.values():
            for other_id, count in bija.co_activated_with.items():
                if count >= self.co_activation_threshold:
                    pair = tuple(sorted([bija.memory_id, other_id]))
                    if pair not in seen:
                        seen.add(pair)
                        associations.append(
                            (pair[0], pair[1], count)
                        )

        return sorted(associations, key=lambda x: x[2], reverse=True)

    def get_strength_adjustments(self) -> Dict[str, float]:
        """Compute strength boosts/penalties based on activation patterns.

        Returns memory_id → adjustment_delta (can be positive or negative).
        The memory pipeline applies these to FadeMem strength.

        This is the key innovation: USE-based strength adjustment
        complements TIME-based decay. A memory can fight decay
        by proving useful.
        """
        adjustments: Dict[str, float] = {}

        for bija in self._bija.values():
            if bija.activation_count == 0 and bija.dormant_queries == 0:
                continue  # no data yet

            ratio = bija.ripening_ratio

            if ratio >= 0.7 and bija.activation_count >= 3:
                # Consistently useful — strengthen
                adjustments[bija.memory_id] = 0.05
            elif ratio <= 0.1 and bija.dormant_queries >= 5:
                # Never useful — weaken (let FadeMem decay faster)
                adjustments[bija.memory_id] = -0.02
            elif bija.strength_gradient < -0.3:
                # Trending negative
                adjustments[bija.memory_id] = -0.01

        return adjustments

    def get_activation_stats(self) -> Dict[str, Any]:
        """Get storehouse statistics."""
        total = len(self._bija)
        if total == 0:
            return {
                "total_seeds": 0,
                "dormant": 0,
                "active": 0,
                "re_extraction_needed": 0,
                "strong_associations": 0,
            }

        dormant = sum(1 for b in self._bija.values() if b.is_dormant)
        active = sum(1 for b in self._bija.values() if b.activation_count > 0)
        re_extract = sum(1 for b in self._bija.values() if b.needs_re_extraction)
        assoc = len(self.get_strong_associations())

        return {
            "total_seeds": total,
            "dormant": dormant,
            "active": active,
            "dormant_ratio": dormant / total,
            "active_ratio": active / total,
            "re_extraction_needed": re_extract,
            "strong_associations": assoc,
            "avg_ripening_ratio": (
                sum(b.ripening_ratio for b in self._bija.values()) / total
            ),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            mid: {
                "activation_count": b.activation_count,
                "dormant_queries": b.dormant_queries,
                "last_activated": b.last_activated,
                "co_activated_with": dict(b.co_activated_with),
                "strength_gradient": b.strength_gradient,
            }
            for mid, b in self._bija.items()
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        """Restore from persisted state."""
        for mid, bdata in data.items():
            bija = self._get_bija(mid)
            bija.activation_count = bdata.get("activation_count", 0)
            bija.dormant_queries = bdata.get("dormant_queries", 0)
            bija.last_activated = bdata.get("last_activated", 0.0)
            bija.co_activated_with = bdata.get("co_activated_with", {})
            bija.strength_gradient = bdata.get("strength_gradient", 0.0)
