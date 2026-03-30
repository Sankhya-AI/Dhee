"""HiveMemory — multi-agent shared cognition on top of engram-bus.

Enables multiple DheePlugin instances (across agents, processes, or machines)
to share and evolve a collective knowledge base:

  - **Shared Insights**: Cross-agent discoveries and patterns.
  - **Shared Heuristics**: Abstract reasoning rules mined from any agent's trajectories.
  - **Shared Skills**: Proven skills that any agent can adopt.
  - **Collective Signals**: Aggregated samskara-like signals for hive-level evolution.

Architecture:
  Each agent runs a local DheePlugin. HiveMemory sits alongside it and
  periodically publishes local discoveries to the bus, and subscribes to
  discoveries from other agents. A quality gate ensures only validated
  knowledge propagates.

  ┌──────────┐     bus.publish()     ┌─────────────┐
  │ Agent A   │ ────────────────────▶ │  engram-bus  │
  │ DheePlugin│ ◀──────────────────── │  (pub/sub +  │
  │ + Hive    │     bus.subscribe()  │   KV store)  │
  └──────────┘                       └──────┬──────┘
                                            │
  ┌──────────┐                              │
  │ Agent B   │ ◀───────────────────────────┘
  │ DheePlugin│
  │ + Hive    │
  └──────────┘
"""

from __future__ import annotations

import json
import logging
import time
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Shared knowledge item types
# ---------------------------------------------------------------------------

@dataclass
class SharedItem:
    """A piece of knowledge shared on the hive."""

    id: str
    kind: str          # "insight" | "heuristic" | "skill" | "signal"
    content: Dict[str, Any]
    source_agent: str
    timestamp: str = field(default_factory=_now_iso)
    confidence: float = 0.5
    votes_up: int = 0
    votes_down: int = 0
    adopted_by: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "source_agent": self.source_agent,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "votes_up": self.votes_up,
            "votes_down": self.votes_down,
            "adopted_by": self.adopted_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SharedItem":
        return cls(
            id=data["id"],
            kind=data["kind"],
            content=data.get("content", {}),
            source_agent=data.get("source_agent", "unknown"),
            timestamp=data.get("timestamp", _now_iso()),
            confidence=data.get("confidence", 0.5),
            votes_up=data.get("votes_up", 0),
            votes_down=data.get("votes_down", 0),
            adopted_by=data.get("adopted_by", []),
        )

    @property
    def quality_score(self) -> float:
        """Wilson score lower bound — conservative estimate of true quality."""
        n = self.votes_up + self.votes_down
        if n == 0:
            return self.confidence
        p = self.votes_up / n
        # Wilson score interval (simplified)
        z = 1.96  # 95% confidence
        denominator = 1 + z * z / n
        centre = p + z * z / (2 * n)
        spread = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5
        return (centre - spread) / denominator


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

_TOPIC_SHARE = "dhee.hive.share"
_TOPIC_VOTE = "dhee.hive.vote"
_TOPIC_ADOPT = "dhee.hive.adopt"
_TOPIC_SYNC_REQUEST = "dhee.hive.sync.request"
_TOPIC_SYNC_RESPONSE = "dhee.hive.sync.response"
_NS_HIVE = "dhee_hive"


# ---------------------------------------------------------------------------
# HiveMemory
# ---------------------------------------------------------------------------

class HiveMemory:
    """Multi-agent shared cognition layer.

    Wraps an engram-bus instance to provide structured knowledge sharing
    with quality gating and adoption tracking.
    """

    def __init__(
        self,
        agent_id: str,
        bus: Any = None,
        min_confidence_to_share: float = 0.4,
        min_quality_to_adopt: float = 0.3,
        auto_subscribe: bool = True,
    ):
        """
        Args:
            agent_id: This agent's identifier on the hive.
            bus: An engram_bus.Bus instance. If None, creates an in-memory bus.
            min_confidence_to_share: Minimum confidence to publish to hive.
            min_quality_to_adopt: Minimum quality_score to auto-adopt shared items.
            auto_subscribe: Whether to subscribe to hive topics on init.
        """
        self.agent_id = agent_id
        self._min_share = min_confidence_to_share
        self._min_adopt = min_quality_to_adopt

        # Local store of hive items (id -> SharedItem)
        self._items: Dict[str, SharedItem] = {}
        self._lock = threading.RLock()
        self._on_receive_callbacks: List[Callable] = []

        # Bus connection
        if bus is None:
            try:
                from engram_bus import Bus
                bus = Bus()
            except ImportError:
                logger.warning("engram-bus not available, hive runs in local-only mode")
                bus = None

        self._bus = bus

        if bus and auto_subscribe:
            self._subscribe()

    def _subscribe(self) -> None:
        """Subscribe to hive topics on the bus."""
        if not self._bus:
            return
        self._bus.subscribe(_TOPIC_SHARE, self._on_share_received, agent=self.agent_id)
        self._bus.subscribe(_TOPIC_VOTE, self._on_vote_received, agent=self.agent_id)
        self._bus.subscribe(_TOPIC_ADOPT, self._on_adopt_received, agent=self.agent_id)
        self._bus.subscribe(
            _TOPIC_SYNC_REQUEST, self._on_sync_request, agent=self.agent_id,
        )
        self._bus.register(self.agent_id, metadata={"type": "dhee_hive_member"})

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def share_insight(
        self,
        insight_id: str,
        content: Dict[str, Any],
        confidence: float = 0.5,
    ) -> Optional[SharedItem]:
        """Share an insight (from Buddhi reflect) with the hive."""
        return self._publish_item(
            item_id=f"insight:{self.agent_id}:{insight_id}",
            kind="insight",
            content=content,
            confidence=confidence,
        )

    def share_heuristic(
        self,
        heuristic_id: str,
        content: Dict[str, Any],
        confidence: float = 0.5,
    ) -> Optional[SharedItem]:
        """Share a distilled heuristic with the hive."""
        return self._publish_item(
            item_id=f"heuristic:{self.agent_id}:{heuristic_id}",
            kind="heuristic",
            content=content,
            confidence=confidence,
        )

    def share_skill(
        self,
        skill_id: str,
        content: Dict[str, Any],
        confidence: float = 0.5,
    ) -> Optional[SharedItem]:
        """Share a proven skill with the hive."""
        return self._publish_item(
            item_id=f"skill:{self.agent_id}:{skill_id}",
            kind="skill",
            content=content,
            confidence=confidence,
        )

    def share_signal(
        self,
        signal_type: str,
        data: Dict[str, Any],
    ) -> Optional[SharedItem]:
        """Share an aggregated signal (e.g., vasana shift) with the hive."""
        return self._publish_item(
            item_id=f"signal:{self.agent_id}:{signal_type}:{int(time.time())}",
            kind="signal",
            content={"signal_type": signal_type, **data},
            confidence=0.5,
        )

    def _publish_item(
        self,
        item_id: str,
        kind: str,
        content: Dict[str, Any],
        confidence: float,
    ) -> Optional[SharedItem]:
        if confidence < self._min_share:
            logger.debug(
                "Not sharing %s (confidence %.2f < %.2f)",
                item_id, confidence, self._min_share,
            )
            return None

        item = SharedItem(
            id=item_id,
            kind=kind,
            content=content,
            source_agent=self.agent_id,
            confidence=confidence,
        )

        with self._lock:
            self._items[item.id] = item

        if self._bus:
            self._bus.publish(_TOPIC_SHARE, item.to_dict(), agent=self.agent_id)
            # Also store in bus KV for late joiners
            self._bus.put(
                f"hive:{item.id}",
                json.dumps(item.to_dict()),
                agent=self.agent_id,
                namespace=_NS_HIVE,
            )

        return item

    # ------------------------------------------------------------------
    # Voting
    # ------------------------------------------------------------------

    def vote(self, item_id: str, upvote: bool = True) -> None:
        """Vote on a shared item's quality."""
        with self._lock:
            item = self._items.get(item_id)
            if item:
                if upvote:
                    item.votes_up += 1
                else:
                    item.votes_down += 1

        if self._bus:
            self._bus.publish(
                _TOPIC_VOTE,
                {"item_id": item_id, "upvote": upvote, "voter": self.agent_id},
                agent=self.agent_id,
            )

    def adopt(self, item_id: str) -> Optional[SharedItem]:
        """Mark a shared item as adopted by this agent."""
        with self._lock:
            item = self._items.get(item_id)
            if not item:
                return None
            if self.agent_id not in item.adopted_by:
                item.adopted_by.append(self.agent_id)

        if self._bus:
            self._bus.publish(
                _TOPIC_ADOPT,
                {"item_id": item_id, "adopter": self.agent_id},
                agent=self.agent_id,
            )
        return item

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_shared(
        self,
        kind: Optional[str] = None,
        min_quality: Optional[float] = None,
        limit: int = 20,
    ) -> List[SharedItem]:
        """Get shared items, optionally filtered by kind and quality."""
        min_q = min_quality if min_quality is not None else 0.0

        with self._lock:
            items = list(self._items.values())

        if kind:
            items = [i for i in items if i.kind == kind]
        items = [i for i in items if i.quality_score >= min_q]
        items.sort(key=lambda i: i.quality_score, reverse=True)
        return items[:limit]

    def get_adoptable(self, limit: int = 10) -> List[SharedItem]:
        """Get high-quality items not yet adopted by this agent."""
        with self._lock:
            candidates = [
                item for item in self._items.values()
                if self.agent_id not in item.adopted_by
                and item.source_agent != self.agent_id
                and item.quality_score >= self._min_adopt
            ]
        candidates.sort(key=lambda i: i.quality_score, reverse=True)
        return candidates[:limit]

    def get_hive_stats(self) -> Dict[str, Any]:
        """Get statistics about the hive."""
        with self._lock:
            items = list(self._items.values())

        by_kind: Dict[str, int] = {}
        by_agent: Dict[str, int] = {}
        total_votes = 0

        for item in items:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
            by_agent[item.source_agent] = by_agent.get(item.source_agent, 0) + 1
            total_votes += item.votes_up + item.votes_down

        return {
            "total_items": len(items),
            "by_kind": by_kind,
            "by_agent": by_agent,
            "total_votes": total_votes,
            "avg_quality": (
                sum(i.quality_score for i in items) / len(items)
                if items else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Bus callbacks
    # ------------------------------------------------------------------

    def _on_share_received(
        self, topic: str, data: Any, sender_agent: Optional[str],
    ) -> None:
        """Handle incoming shared item from another agent."""
        if sender_agent == self.agent_id:
            return  # Ignore own messages

        try:
            item = SharedItem.from_dict(data)
        except (TypeError, KeyError) as e:
            logger.debug("Invalid shared item: %s", e)
            return

        with self._lock:
            if item.id not in self._items:
                self._items[item.id] = item

        for cb in self._on_receive_callbacks:
            try:
                cb(item)
            except Exception as e:
                logger.debug("Hive receive callback error: %s", e)

    def _on_vote_received(
        self, topic: str, data: Any, sender_agent: Optional[str],
    ) -> None:
        """Handle incoming vote from another agent."""
        if sender_agent == self.agent_id:
            return

        item_id = data.get("item_id")
        upvote = data.get("upvote", True)

        with self._lock:
            item = self._items.get(item_id)
            if item:
                if upvote:
                    item.votes_up += 1
                else:
                    item.votes_down += 1

    def _on_adopt_received(
        self, topic: str, data: Any, sender_agent: Optional[str],
    ) -> None:
        """Handle adoption notification from another agent."""
        item_id = data.get("item_id")
        adopter = data.get("adopter")

        with self._lock:
            item = self._items.get(item_id)
            if item and adopter and adopter not in item.adopted_by:
                item.adopted_by.append(adopter)

    def _on_sync_request(
        self, topic: str, data: Any, sender_agent: Optional[str],
    ) -> None:
        """Respond to sync request from another agent (e.g., edge coming online)."""
        if sender_agent == self.agent_id or not self._bus:
            return

        # Send all our items as a sync response
        with self._lock:
            payload = {item_id: item.to_dict() for item_id, item in self._items.items()}

        self._bus.publish(
            _TOPIC_SYNC_RESPONSE,
            {"items": payload, "responder": self.agent_id},
            agent=self.agent_id,
        )

    # ------------------------------------------------------------------
    # Sync (pull-based)
    # ------------------------------------------------------------------

    def request_sync(self) -> None:
        """Request a full sync from other hive members (e.g., after coming online)."""
        if not self._bus:
            return
        self._bus.subscribe(
            _TOPIC_SYNC_RESPONSE, self._on_sync_response, agent=self.agent_id,
        )
        self._bus.publish(
            _TOPIC_SYNC_REQUEST,
            {"requester": self.agent_id},
            agent=self.agent_id,
        )

    def _on_sync_response(
        self, topic: str, data: Any, sender_agent: Optional[str],
    ) -> None:
        """Handle sync response — merge received items."""
        items_data = data.get("items", {})
        with self._lock:
            for item_id, item_dict in items_data.items():
                if item_id not in self._items:
                    try:
                        self._items[item_id] = SharedItem.from_dict(item_dict)
                    except (TypeError, KeyError):
                        pass

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_receive(self, callback: Callable[[SharedItem], None]) -> None:
        """Register a callback for when new items arrive from the hive."""
        self._on_receive_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Export for DheePlugin integration
    # ------------------------------------------------------------------

    def get_context_block(self, limit: int = 5) -> Dict[str, Any]:
        """Get hive knowledge formatted for HyperContext injection."""
        insights = self.get_shared(kind="insight", limit=limit)
        heuristics = self.get_shared(kind="heuristic", limit=limit)
        skills = self.get_shared(kind="skill", limit=limit)

        return {
            "hive_insights": [
                {
                    "source": i.source_agent,
                    "content": i.content,
                    "quality": round(i.quality_score, 2),
                }
                for i in insights
            ],
            "hive_heuristics": [
                {
                    "source": h.source_agent,
                    "content": h.content,
                    "quality": round(h.quality_score, 2),
                }
                for h in heuristics
            ],
            "hive_skills": [
                {
                    "source": s.source_agent,
                    "name": s.content.get("name", s.id),
                    "quality": round(s.quality_score, 2),
                }
                for s in skills
            ],
        }

    def close(self) -> None:
        """Unsubscribe and clean up."""
        # Bus cleanup is handled by the bus owner
        self._on_receive_callbacks.clear()
