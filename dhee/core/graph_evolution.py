"""Evolving knowledge graph — versioned entities, personalized PageRank, schema-free extraction.

Extends KnowledgeGraph (graph.py) with three capabilities:

1. **Entity versioning**: Every entity mutation is stored as a version snapshot.
   Queries can ask "what was X at time T?" and diffs show how entities evolve.

2. **Personalized PageRank**: Per-user / per-agent importance scores over
   the entity–memory graph. Guides retrieval toward what matters *to this user*.

3. **Schema-free extraction**: Uses BuddhiMini (or any LLM) to discover
   entity types at runtime, stored as EntityType.DYNAMIC with a type_label.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from dhee.core.graph import (
    Entity,
    EntityType,
    KnowledgeGraph,
    Relationship,
    RelationType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entity Versioning
# ---------------------------------------------------------------------------

@dataclass
class EntityVersion:
    """A point-in-time snapshot of an entity's state."""

    entity_name: str
    version: int
    timestamp: str  # ISO-8601
    entity_type: str
    type_label: Optional[str] = None  # For DYNAMIC entities
    aliases: List[str] = field(default_factory=list)
    memory_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    change_reason: str = ""  # What triggered this version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_name": self.entity_name,
            "version": self.version,
            "timestamp": self.timestamp,
            "entity_type": self.entity_type,
            "type_label": self.type_label,
            "aliases": self.aliases,
            "memory_ids": self.memory_ids,
            "metadata": self.metadata,
            "change_reason": self.change_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EntityVersion":
        return cls(
            entity_name=data["entity_name"],
            version=data["version"],
            timestamp=data["timestamp"],
            entity_type=data["entity_type"],
            type_label=data.get("type_label"),
            aliases=data.get("aliases", []),
            memory_ids=data.get("memory_ids", []),
            metadata=data.get("metadata", {}),
            change_reason=data.get("change_reason", ""),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EntityVersionStore:
    """Append-only version log for entity snapshots.

    Stored as JSONL on disk — one line per version, O(1) append.
    """

    def __init__(self, path: str):
        self._path = path
        self._versions: Dict[str, List[EntityVersion]] = defaultdict(list)
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    v = EntityVersion.from_dict(json.loads(line))
                    self._versions[v.entity_name].append(v)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load entity versions: %s", e)

    def record(self, entity: Entity, reason: str = "") -> EntityVersion:
        """Snapshot the current state of an entity."""
        history = self._versions[entity.name]
        version_num = (history[-1].version + 1) if history else 1

        v = EntityVersion(
            entity_name=entity.name,
            version=version_num,
            timestamp=_now_iso(),
            entity_type=entity.entity_type.value,
            type_label=entity.metadata.get("type_label"),
            aliases=sorted(entity.aliases),
            memory_ids=sorted(entity.memory_ids),
            metadata=dict(entity.metadata),
            change_reason=reason,
        )

        history.append(v)
        self._append(v)
        return v

    def _append(self, v: EntityVersion) -> None:
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(v.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("Failed to persist entity version: %s", e)

    def get_history(self, entity_name: str) -> List[EntityVersion]:
        """All versions of an entity, oldest first."""
        return list(self._versions.get(entity_name, []))

    def get_at_time(self, entity_name: str, iso_time: str) -> Optional[EntityVersion]:
        """Get the entity version that was current at a given time."""
        history = self._versions.get(entity_name, [])
        result = None
        for v in history:
            if v.timestamp <= iso_time:
                result = v
            else:
                break
        return result

    def diff(self, entity_name: str, v1: int, v2: int) -> Dict[str, Any]:
        """Compute the difference between two versions of an entity."""
        history = self._versions.get(entity_name, [])
        ver_map = {v.version: v for v in history}
        old = ver_map.get(v1)
        new = ver_map.get(v2)
        if not old or not new:
            return {"error": "version not found"}

        changes: Dict[str, Any] = {}
        if old.entity_type != new.entity_type:
            changes["entity_type"] = {"old": old.entity_type, "new": new.entity_type}
        if old.type_label != new.type_label:
            changes["type_label"] = {"old": old.type_label, "new": new.type_label}

        old_aliases = set(old.aliases)
        new_aliases = set(new.aliases)
        if old_aliases != new_aliases:
            changes["aliases_added"] = sorted(new_aliases - old_aliases)
            changes["aliases_removed"] = sorted(old_aliases - new_aliases)

        old_mids = set(old.memory_ids)
        new_mids = set(new.memory_ids)
        if old_mids != new_mids:
            changes["memories_added"] = sorted(new_mids - old_mids)
            changes["memories_removed"] = sorted(old_mids - new_mids)

        # Metadata diff (shallow)
        for key in set(old.metadata) | set(new.metadata):
            old_val = old.metadata.get(key)
            new_val = new.metadata.get(key)
            if old_val != new_val:
                changes.setdefault("metadata", {})[key] = {
                    "old": old_val, "new": new_val,
                }

        return {
            "entity": entity_name,
            "from_version": v1,
            "to_version": v2,
            "changes": changes,
        }

    @property
    def entity_count(self) -> int:
        return len(self._versions)


# ---------------------------------------------------------------------------
# Personalized PageRank
# ---------------------------------------------------------------------------

class PersonalizedPageRank:
    """Per-user importance ranking over the entity–memory graph.

    Runs a standard power-iteration PageRank seeded from a user's
    interaction history (memories they wrote, entities they mentioned).
    Results are cached and refreshed when the graph changes.
    """

    def __init__(
        self,
        damping: float = 0.85,
        iterations: int = 20,
        tolerance: float = 1e-6,
    ):
        self.damping = damping
        self.iterations = iterations
        self.tolerance = tolerance
        self._cache: Dict[str, Dict[str, float]] = {}  # user_id -> {node -> score}

    def compute(
        self,
        graph: KnowledgeGraph,
        seed_memory_ids: Optional[Set[str]] = None,
        user_id: str = "default",
    ) -> Dict[str, float]:
        """Compute personalized PageRank for a user.

        Args:
            graph: The knowledge graph.
            seed_memory_ids: Memories that form the user's personalization
                vector. If None, uses all memories.
            user_id: Cache key.

        Returns:
            Dict mapping node IDs (memory IDs and "entity:name") to scores.
        """
        # Build adjacency from relationships
        adj: Dict[str, Set[str]] = defaultdict(set)
        all_nodes: Set[str] = set()

        for rel in graph.relationships:
            adj[rel.source_id].add(rel.target_id)
            adj[rel.target_id].add(rel.source_id)
            all_nodes.add(rel.source_id)
            all_nodes.add(rel.target_id)

        # Add entity nodes
        for entity_name, entity in graph.entities.items():
            enode = f"entity:{entity_name}"
            all_nodes.add(enode)
            for mid in entity.memory_ids:
                adj[mid].add(enode)
                adj[enode].add(mid)
                all_nodes.add(mid)

        if not all_nodes:
            return {}

        n = len(all_nodes)
        node_list = sorted(all_nodes)
        node_idx = {node: i for i, node in enumerate(node_list)}

        # Personalization vector: uniform over seeds, zero elsewhere
        personalization = [0.0] * n
        if seed_memory_ids:
            seeds_in_graph = [
                node_idx[mid] for mid in seed_memory_ids if mid in node_idx
            ]
            if seeds_in_graph:
                weight = 1.0 / len(seeds_in_graph)
                for idx in seeds_in_graph:
                    personalization[idx] = weight
            else:
                personalization = [1.0 / n] * n
        else:
            personalization = [1.0 / n] * n

        # Power iteration
        scores = [1.0 / n] * n
        for _ in range(self.iterations):
            new_scores = [0.0] * n
            for i, node in enumerate(node_list):
                neighbors = adj.get(node, set())
                if not neighbors:
                    # Dangling node — distribute uniformly
                    share = scores[i] / n
                    for j in range(n):
                        new_scores[j] += share
                else:
                    share = scores[i] / len(neighbors)
                    for nb in neighbors:
                        if nb in node_idx:
                            new_scores[node_idx[nb]] += share

            # Apply damping + personalization
            for i in range(n):
                new_scores[i] = (
                    (1 - self.damping) * personalization[i]
                    + self.damping * new_scores[i]
                )

            # Check convergence
            delta = sum(abs(new_scores[i] - scores[i]) for i in range(n))
            scores = new_scores
            if delta < self.tolerance:
                break

        result = {node_list[i]: scores[i] for i in range(n)}
        self._cache[user_id] = result
        return result

    def get_top_entities(
        self,
        graph: KnowledgeGraph,
        user_id: str = "default",
        seed_memory_ids: Optional[Set[str]] = None,
        limit: int = 20,
    ) -> List[Tuple[str, float]]:
        """Get top-ranked entities for a user."""
        if user_id not in self._cache:
            self.compute(graph, seed_memory_ids=seed_memory_ids, user_id=user_id)

        scores = self._cache.get(user_id, {})
        entity_scores = [
            (name.replace("entity:", ""), score)
            for name, score in scores.items()
            if name.startswith("entity:")
        ]
        entity_scores.sort(key=lambda x: x[1], reverse=True)
        return entity_scores[:limit]

    def boost_retrieval(
        self,
        memory_ids: List[str],
        user_id: str = "default",
    ) -> Dict[str, float]:
        """Get PageRank boost factors for a set of candidate memory IDs."""
        scores = self._cache.get(user_id, {})
        if not scores:
            return {}
        return {mid: scores.get(mid, 0.0) for mid in memory_ids}

    def invalidate(self, user_id: Optional[str] = None) -> None:
        """Clear cached scores. Call when graph changes."""
        if user_id:
            self._cache.pop(user_id, None)
        else:
            self._cache.clear()


# ---------------------------------------------------------------------------
# Schema-Free Entity Extraction
# ---------------------------------------------------------------------------

_SCHEMA_FREE_PROMPT = """Extract entities from the following text. For each entity, provide:
- name: The entity name
- type: A descriptive type (e.g., "person", "technology", "framework", "metric",
  "emotion", "event", "disease", "recipe" — any type that fits, not limited to a fixed set)
- relevance: How important this entity is to the text (0.0 to 1.0)

Text: {content}

Return a JSON array. Example:
[{{"name": "FastAPI", "type": "framework", "relevance": 0.9}}]

Return ONLY the JSON array:"""


def extract_entities_schema_free(
    content: str,
    memory_id: str,
    graph: KnowledgeGraph,
    llm: Any = None,
    min_relevance: float = 0.3,
) -> List[Entity]:
    """Extract entities without a fixed type schema.

    Uses LLM to discover entity types at runtime. Discovered types are
    stored as EntityType.DYNAMIC with a ``type_label`` in metadata.

    Falls back to graph.extract_entities() regex path if no LLM.
    """
    if not llm:
        return graph.extract_entities(content, memory_id, use_llm=False)

    prompt = _SCHEMA_FREE_PROMPT.format(content=content[:2000])

    try:
        response = llm.generate(prompt)
        arr_start = response.find("[")
        if arr_start < 0:
            return graph.extract_entities(content, memory_id, use_llm=False)

        items, _ = json.JSONDecoder().raw_decode(response, arr_start)
    except Exception as e:
        logger.debug("Schema-free extraction failed (%s), falling back to regex", e)
        return graph.extract_entities(content, memory_id, use_llm=False)

    # Known EntityType values (lowercase)
    _known_types = {t.value for t in EntityType}

    entities: List[Entity] = []
    for item in items:
        name = item.get("name", "").strip()
        if not name:
            continue

        relevance = float(item.get("relevance", 0.5))
        if relevance < min_relevance:
            continue

        raw_type = item.get("type", "unknown").strip().lower()

        # Map to existing enum if possible; otherwise DYNAMIC
        if raw_type in _known_types:
            entity_type = EntityType(raw_type)
            type_label = None
        else:
            entity_type = EntityType.DYNAMIC
            type_label = raw_type

        entity = graph._get_or_create_entity(name, entity_type)
        entity.memory_ids.add(memory_id)
        entity.metadata["relevance"] = max(
            entity.metadata.get("relevance", 0.0), relevance,
        )
        if type_label:
            entity.metadata["type_label"] = type_label

        entities.append(entity)

    graph.memory_entities[memory_id] = {e.name for e in entities}
    return entities


# ---------------------------------------------------------------------------
# EvolvingGraph — wraps it all together
# ---------------------------------------------------------------------------

class EvolvingGraph:
    """Knowledge graph with entity versioning, PageRank, and schema-free extraction.

    Drop-in extension of KnowledgeGraph — delegates core graph operations
    and adds evolution capabilities on top.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        llm: Any = None,
        damping: float = 0.85,
    ):
        self._data_dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "graph",
        )
        os.makedirs(self._data_dir, exist_ok=True)

        graph_path = os.path.join(self._data_dir, "graph.json")
        self.graph = KnowledgeGraph.load(graph_path, llm=llm)

        self._versions = EntityVersionStore(
            os.path.join(self._data_dir, "entity_versions.jsonl"),
        )
        self._pagerank = PersonalizedPageRank(damping=damping)
        self._llm = llm

    # ── Entity operations (versioned) ──

    def extract_and_version(
        self,
        content: str,
        memory_id: str,
        reason: str = "new_memory",
        schema_free: bool = True,
    ) -> List[Entity]:
        """Extract entities from content and record versions for any changes."""
        if schema_free and self._llm:
            entities = extract_entities_schema_free(
                content, memory_id, self.graph, llm=self._llm,
            )
        else:
            entities = self.graph.extract_entities(content, memory_id)

        # Record version for each entity that was touched
        for entity in entities:
            self._versions.record(entity, reason=reason)

        # Invalidate PageRank caches (graph changed)
        self._pagerank.invalidate()

        return entities

    def update_entity(
        self,
        entity_name: str,
        updates: Dict[str, Any],
        reason: str = "update",
    ) -> Optional[Entity]:
        """Update an entity's fields and record the version."""
        entity = self.graph.entities.get(entity_name)
        if not entity:
            return None

        if "entity_type" in updates:
            entity.entity_type = EntityType(updates["entity_type"])
        if "aliases" in updates:
            entity.aliases.update(updates["aliases"])
        if "metadata" in updates:
            entity.metadata.update(updates["metadata"])

        self._versions.record(entity, reason=reason)
        self._pagerank.invalidate()
        return entity

    def get_entity_history(self, entity_name: str) -> List[EntityVersion]:
        return self._versions.get_history(entity_name)

    def get_entity_at_time(
        self, entity_name: str, iso_time: str,
    ) -> Optional[EntityVersion]:
        return self._versions.get_at_time(entity_name, iso_time)

    def entity_diff(
        self, entity_name: str, v1: int, v2: int,
    ) -> Dict[str, Any]:
        return self._versions.diff(entity_name, v1, v2)

    # ── PageRank ──

    def compute_pagerank(
        self,
        user_id: str = "default",
        seed_memory_ids: Optional[Set[str]] = None,
    ) -> Dict[str, float]:
        return self._pagerank.compute(
            self.graph, seed_memory_ids=seed_memory_ids, user_id=user_id,
        )

    def get_important_entities(
        self,
        user_id: str = "default",
        seed_memory_ids: Optional[Set[str]] = None,
        limit: int = 20,
    ) -> List[Tuple[str, float]]:
        return self._pagerank.get_top_entities(
            self.graph, user_id=user_id,
            seed_memory_ids=seed_memory_ids, limit=limit,
        )

    def pagerank_boost(
        self,
        memory_ids: List[str],
        user_id: str = "default",
    ) -> Dict[str, float]:
        return self._pagerank.boost_retrieval(memory_ids, user_id=user_id)

    # ── Graph delegation ──

    def add_relationship(self, *args, **kwargs) -> Relationship:
        rel = self.graph.add_relationship(*args, **kwargs)
        self._pagerank.invalidate()
        return rel

    def link_by_shared_entities(self, memory_id: str) -> List[Relationship]:
        rels = self.graph.link_by_shared_entities(memory_id)
        if rels:
            self._pagerank.invalidate()
        return rels

    def get_related_memories(self, *args, **kwargs):
        return self.graph.get_related_memories(*args, **kwargs)

    def get_causal_chain(self, *args, **kwargs):
        return self.graph.get_causal_chain(*args, **kwargs)

    def get_memory_graph(self, memory_id: str) -> Dict[str, Any]:
        return self.graph.get_memory_graph(memory_id)

    # ── Persistence ──

    def save(self) -> None:
        """Persist graph to disk. Version store auto-persists on append."""
        graph_path = os.path.join(self._data_dir, "graph.json")
        self.graph.save(graph_path)

    def stats(self) -> Dict[str, Any]:
        base = self.graph.stats()
        base["versioned_entities"] = self._versions.entity_count
        base["dynamic_entities"] = sum(
            1 for e in self.graph.entities.values()
            if e.entity_type == EntityType.DYNAMIC
        )
        return base
