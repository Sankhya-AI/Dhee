"""CRDT-based sync protocol for offline/edge Dhee nodes.

When a DheeEdge instance operates offline (e.g., a humanoid robot in a
warehouse with no connectivity), it accumulates local hive items. On
reconnection, it needs to merge with the central hive without conflicts.

This module implements:
  1. **LWW-Register** (Last-Writer-Wins) for individual shared items.
  2. **G-Counter** for vote counts (grow-only, merge = max per node).
  3. **OR-Set** (Observed-Remove) for adoption lists.
  4. **SyncEnvelope** — wire format for shipping CRDT state between nodes.

Usage:
    # On edge device:
    state = CRDTState(node_id="edge-1")
    state.set_item(shared_item)
    state.increment_votes_up("item:123")
    envelope = state.export_envelope()

    # Ship envelope (HTTP, BLE, serial, file drop — whatever works)

    # On hub:
    hub_state = CRDTState(node_id="hub")
    hub_state.merge(envelope)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _hlc_now(node_id: str) -> str:
    """Hybrid Logical Clock timestamp: <unix_ms>|<node_id>.

    Provides a globally-unique, monotonically-increasing timestamp even
    if wall clocks disagree between nodes. Uses '|' separator so node_ids
    can contain dashes.
    """
    return f"{int(time.time() * 1000)}|{node_id}"


def _hlc_compare(a: str, b: str) -> int:
    """Compare two HLC timestamps. Returns -1, 0, or 1."""
    a_ms, a_node = a.split("|", 1)
    b_ms, b_node = b.split("|", 1)
    a_int, b_int = int(a_ms), int(b_ms)
    if a_int != b_int:
        return -1 if a_int < b_int else 1
    if a_node < b_node:
        return -1
    if a_node > b_node:
        return 1
    return 0


# ---------------------------------------------------------------------------
# LWW-Register: per-item state
# ---------------------------------------------------------------------------

@dataclass
class LWWRegister:
    """Last-Writer-Wins Register for a shared item's content."""

    value: Dict[str, Any]
    timestamp: str  # HLC timestamp
    node_id: str

    def merge(self, other: "LWWRegister") -> "LWWRegister":
        """Merge two registers — latest timestamp wins."""
        if _hlc_compare(self.timestamp, other.timestamp) >= 0:
            return self
        return other

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "timestamp": self.timestamp,
            "node_id": self.node_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LWWRegister":
        return cls(
            value=data["value"],
            timestamp=data["timestamp"],
            node_id=data["node_id"],
        )


# ---------------------------------------------------------------------------
# G-Counter: grow-only counter per node
# ---------------------------------------------------------------------------

@dataclass
class GCounter:
    """Grow-only counter — each node has its own monotonic count."""

    counts: Dict[str, int] = field(default_factory=dict)  # node_id -> count

    @property
    def value(self) -> int:
        return sum(self.counts.values())

    def increment(self, node_id: str, amount: int = 1) -> None:
        self.counts[node_id] = self.counts.get(node_id, 0) + amount

    def merge(self, other: "GCounter") -> "GCounter":
        """Merge = max of each node's count."""
        all_nodes = set(self.counts) | set(other.counts)
        merged = GCounter()
        for node in all_nodes:
            merged.counts[node] = max(
                self.counts.get(node, 0),
                other.counts.get(node, 0),
            )
        return merged

    def to_dict(self) -> Dict[str, int]:
        return dict(self.counts)

    @classmethod
    def from_dict(cls, data: Dict[str, int]) -> "GCounter":
        return cls(counts=dict(data))


# ---------------------------------------------------------------------------
# OR-Set: Observed-Remove Set (for adoption lists)
# ---------------------------------------------------------------------------

@dataclass
class ORSet:
    """Observed-Remove Set — supports both add and remove with convergence.

    Each element is tagged with a unique (node_id, seq) pair. Removes
    only remove the tags that were observed, so concurrent adds win.
    """

    # element -> set of (node_id, seq) tags
    _elements: Dict[str, Set[Tuple[str, int]]] = field(default_factory=lambda: {})
    _tombstones: Set[Tuple[str, int]] = field(default_factory=set)
    _seq: int = 0

    def add(self, element: str, node_id: str) -> None:
        self._seq += 1
        tag = (node_id, self._seq)
        if element not in self._elements:
            self._elements[element] = set()
        self._elements[element].add(tag)

    def remove(self, element: str) -> None:
        tags = self._elements.pop(element, set())
        self._tombstones.update(tags)

    @property
    def elements(self) -> Set[str]:
        return {
            elem for elem, tags in self._elements.items()
            if tags - self._tombstones
        }

    def merge(self, other: "ORSet") -> "ORSet":
        """Merge two OR-Sets."""
        merged = ORSet()
        merged._seq = max(self._seq, other._seq)
        merged._tombstones = self._tombstones | other._tombstones

        all_elements = set(self._elements) | set(other._elements)
        for elem in all_elements:
            tags_a = self._elements.get(elem, set())
            tags_b = other._elements.get(elem, set())
            # Union of live tags minus all tombstones
            live_tags = (tags_a | tags_b) - merged._tombstones
            if live_tags:
                merged._elements[elem] = live_tags

        return merged

    def to_dict(self) -> Dict[str, Any]:
        return {
            "elements": {
                elem: [list(t) for t in tags]
                for elem, tags in self._elements.items()
            },
            "tombstones": [list(t) for t in self._tombstones],
            "seq": self._seq,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ORSet":
        s = cls()
        s._seq = data.get("seq", 0)
        s._tombstones = {tuple(t) for t in data.get("tombstones", [])}
        s._elements = {
            elem: {tuple(t) for t in tags}
            for elem, tags in data.get("elements", {}).items()
        }
        return s


# ---------------------------------------------------------------------------
# Per-item CRDT state
# ---------------------------------------------------------------------------

@dataclass
class ItemCRDT:
    """CRDT state for a single shared hive item."""

    item_id: str
    content: LWWRegister       # The item payload
    votes_up: GCounter = field(default_factory=GCounter)
    votes_down: GCounter = field(default_factory=GCounter)
    adopted_by: ORSet = field(default_factory=ORSet)

    def merge(self, other: "ItemCRDT") -> "ItemCRDT":
        assert self.item_id == other.item_id
        return ItemCRDT(
            item_id=self.item_id,
            content=self.content.merge(other.content),
            votes_up=self.votes_up.merge(other.votes_up),
            votes_down=self.votes_down.merge(other.votes_down),
            adopted_by=self.adopted_by.merge(other.adopted_by),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "content": self.content.to_dict(),
            "votes_up": self.votes_up.to_dict(),
            "votes_down": self.votes_down.to_dict(),
            "adopted_by": self.adopted_by.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ItemCRDT":
        return cls(
            item_id=data["item_id"],
            content=LWWRegister.from_dict(data["content"]),
            votes_up=GCounter.from_dict(data.get("votes_up", {})),
            votes_down=GCounter.from_dict(data.get("votes_down", {})),
            adopted_by=ORSet.from_dict(data.get("adopted_by", {})),
        )


# ---------------------------------------------------------------------------
# SyncEnvelope — wire format
# ---------------------------------------------------------------------------

@dataclass
class SyncEnvelope:
    """Wire format for CRDT state exchange between nodes."""

    source_node: str
    timestamp: str  # HLC
    items: Dict[str, Dict[str, Any]]  # item_id -> ItemCRDT.to_dict()

    def to_bytes(self) -> bytes:
        return json.dumps({
            "source_node": self.source_node,
            "timestamp": self.timestamp,
            "items": self.items,
        }, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "SyncEnvelope":
        d = json.loads(data.decode("utf-8"))
        return cls(
            source_node=d["source_node"],
            timestamp=d["timestamp"],
            items=d.get("items", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_node": self.source_node,
            "timestamp": self.timestamp,
            "items": self.items,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncEnvelope":
        return cls(
            source_node=data["source_node"],
            timestamp=data["timestamp"],
            items=data.get("items", {}),
        )


# ---------------------------------------------------------------------------
# CRDTState — per-node state manager
# ---------------------------------------------------------------------------

class CRDTState:
    """Manages CRDT state for a single node.

    Each node maintains its own CRDTState. On sync, nodes exchange
    SyncEnvelopes and merge them — convergence is guaranteed by the
    CRDT merge semantics (commutative, associative, idempotent).
    """

    def __init__(self, node_id: str, persist_path: Optional[str] = None):
        self.node_id = node_id
        self._items: Dict[str, ItemCRDT] = {}
        self._persist_path = persist_path
        if persist_path:
            self._load()

    def set_item(self, item_id: str, content: Dict[str, Any]) -> None:
        """Set or update an item's content (LWW)."""
        ts = _hlc_now(self.node_id)
        register = LWWRegister(value=content, timestamp=ts, node_id=self.node_id)

        if item_id in self._items:
            self._items[item_id].content = self._items[item_id].content.merge(register)
        else:
            self._items[item_id] = ItemCRDT(item_id=item_id, content=register)

        self._auto_persist()

    def increment_votes_up(self, item_id: str, amount: int = 1) -> None:
        if item_id in self._items:
            self._items[item_id].votes_up.increment(self.node_id, amount)
            self._auto_persist()

    def increment_votes_down(self, item_id: str, amount: int = 1) -> None:
        if item_id in self._items:
            self._items[item_id].votes_down.increment(self.node_id, amount)
            self._auto_persist()

    def add_adopter(self, item_id: str, adopter: str) -> None:
        if item_id in self._items:
            self._items[item_id].adopted_by.add(adopter, self.node_id)
            self._auto_persist()

    def export_envelope(self) -> SyncEnvelope:
        """Export current state as a sync envelope."""
        return SyncEnvelope(
            source_node=self.node_id,
            timestamp=_hlc_now(self.node_id),
            items={
                item_id: crdt.to_dict()
                for item_id, crdt in self._items.items()
            },
        )

    def merge(self, envelope: SyncEnvelope) -> int:
        """Merge a received envelope into local state.

        Returns number of items updated.
        """
        updated = 0
        for item_id, item_dict in envelope.items.items():
            try:
                remote = ItemCRDT.from_dict(item_dict)
            except (TypeError, KeyError) as e:
                logger.debug("Skipping malformed item %s: %s", item_id, e)
                continue

            if item_id in self._items:
                merged = self._items[item_id].merge(remote)
                # Check if anything actually changed
                if merged.to_dict() != self._items[item_id].to_dict():
                    self._items[item_id] = merged
                    updated += 1
            else:
                self._items[item_id] = remote
                updated += 1

        if updated:
            self._auto_persist()
        return updated

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get resolved state of an item (content + vote totals + adopters)."""
        crdt = self._items.get(item_id)
        if not crdt:
            return None
        return {
            "item_id": item_id,
            "content": crdt.content.value,
            "votes_up": crdt.votes_up.value,
            "votes_down": crdt.votes_down.value,
            "adopted_by": sorted(crdt.adopted_by.elements),
            "last_updated": crdt.content.timestamp,
        }

    def list_items(self) -> List[Dict[str, Any]]:
        """List all items with resolved state."""
        return [
            self.get_item(item_id)
            for item_id in sorted(self._items)
        ]

    @property
    def item_count(self) -> int:
        return len(self._items)

    # ── Persistence ──

    def _auto_persist(self) -> None:
        if self._persist_path:
            self.save()

    def save(self, path: Optional[str] = None) -> None:
        path = path or self._persist_path
        if not path:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        data = {
            "node_id": self.node_id,
            "items": {
                item_id: crdt.to_dict()
                for item_id, crdt in self._items.items()
            },
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _load(self) -> None:
        if not self._persist_path or not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item_id, item_dict in data.get("items", {}).items():
                self._items[item_id] = ItemCRDT.from_dict(item_dict)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load CRDT state: %s", e)
