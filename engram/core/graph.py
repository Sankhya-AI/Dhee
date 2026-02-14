"""Knowledge graph for memory relationships.

Provides entity extraction and relationship linking between memories,
enabling graph-based retrieval and reasoning.
"""

from __future__ import annotations

import re
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class RelationType(str, Enum):
    """Types of relationships between memories."""
    SHARED_ENTITY = "shared_entity"      # Memories share the same entity
    RELATED_TO = "related_to"            # General relationship
    CONTRADICTS = "contradicts"          # Memory contradicts another
    SUPERSEDES = "supersedes"            # Memory replaces/updates another
    ELABORATES = "elaborates"            # Memory adds detail to another
    CAUSED_BY = "caused_by"              # Causal relationship
    DEPENDS_ON = "depends_on"            # Dependency relationship


class EntityType(str, Enum):
    """Types of entities that can be extracted."""
    PERSON = "person"
    ORGANIZATION = "organization"
    TECHNOLOGY = "technology"
    CONCEPT = "concept"
    LOCATION = "location"
    PROJECT = "project"
    TOOL = "tool"
    PREFERENCE = "preference"
    UNKNOWN = "unknown"


@dataclass
class Entity:
    """An entity extracted from a memory."""
    name: str
    entity_type: EntityType = EntityType.UNKNOWN
    aliases: Set[str] = field(default_factory=set)
    memory_ids: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.entity_type.value,
            "aliases": list(self.aliases),
            "memory_ids": list(self.memory_ids),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entity":
        return cls(
            name=data["name"],
            entity_type=EntityType(data.get("type", "unknown")),
            aliases=set(data.get("aliases", [])),
            memory_ids=set(data.get("memory_ids", [])),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Relationship:
    """A relationship between two memories."""
    source_id: str
    target_id: str
    relation_type: RelationType
    entity: Optional[str] = None  # Entity that links them (for SHARED_ENTITY)
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type.value,
            "entity": self.entity,
            "weight": self.weight,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Relationship":
        return cls(
            source_id=data["source_id"],
            target_id=data["target_id"],
            relation_type=RelationType(data["relation_type"]),
            entity=data.get("entity"),
            weight=data.get("weight", 1.0),
            metadata=data.get("metadata", {}),
        )


class KnowledgeGraph:
    """In-memory knowledge graph for memory relationships.

    Stores entities and relationships between memories for graph-based retrieval.
    """

    def __init__(self, llm=None):
        """Initialize the knowledge graph.

        Args:
            llm: Optional LLM instance for advanced entity extraction.
        """
        self.llm = llm
        self.entities: Dict[str, Entity] = {}  # entity_name -> Entity
        self.relationships: List[Relationship] = []
        self.memory_entities: Dict[str, Set[str]] = {}  # memory_id -> entity_names
        self.memory_relations: Dict[str, List[Relationship]] = {}  # memory_id -> relationships

    def extract_entities(
        self,
        content: str,
        memory_id: str,
        use_llm: bool = False,
    ) -> List[Entity]:
        """Extract entities from memory content.

        Args:
            content: Memory content text
            memory_id: ID of the memory
            use_llm: Whether to use LLM for extraction (more accurate but slower)

        Returns:
            List of extracted entities
        """
        if use_llm and self.llm:
            return self._extract_entities_llm(content, memory_id)
        return self._extract_entities_regex(content, memory_id)

    def _extract_entities_regex(self, content: str, memory_id: str) -> List[Entity]:
        """Extract entities using regex patterns (fast, less accurate)."""
        entities = []
        content_lower = content.lower()

        # Technology/tool patterns
        tech_patterns = [
            r'\b(python|javascript|typescript|rust|go|java|c\+\+|ruby|php|swift|kotlin)\b',
            r'\b(react|vue|angular|svelte|django|flask|fastapi|express|nextjs|nuxt)\b',
            r'\b(docker|kubernetes|aws|gcp|azure|terraform|ansible)\b',
            r'\b(postgresql|mysql|mongodb|redis|elasticsearch|sqlite)\b',
            r'\b(git|github|gitlab|vscode|cursor|vim|neovim|emacs)\b',
        ]

        for pattern in tech_patterns:
            for match in re.finditer(pattern, content_lower):
                name = match.group(1)
                entity = self._get_or_create_entity(name, EntityType.TECHNOLOGY)
                entity.memory_ids.add(memory_id)
                entities.append(entity)

        # Capitalized words (potential names, projects, organizations)
        cap_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b'
        for match in re.finditer(cap_pattern, content):
            name = match.group(1)
            if len(name) > 2 and name.lower() not in ['the', 'this', 'that', 'user']:
                entity = self._get_or_create_entity(name, EntityType.UNKNOWN)
                entity.memory_ids.add(memory_id)
                entities.append(entity)

        # Preference patterns
        pref_patterns = [
            r'(?:prefers?|likes?|loves?|uses?)\s+([a-zA-Z0-9_-]+)',
            r'(?:favorite|preferred)\s+(?:\w+\s+)?is\s+([a-zA-Z0-9_-]+)',
        ]
        for pattern in pref_patterns:
            for match in re.finditer(pattern, content_lower):
                name = match.group(1)
                entity = self._get_or_create_entity(name, EntityType.PREFERENCE)
                entity.memory_ids.add(memory_id)
                entities.append(entity)

        # Update memory -> entities mapping
        self.memory_entities[memory_id] = {e.name for e in entities}

        return entities

    def _extract_entities_llm(self, content: str, memory_id: str) -> List[Entity]:
        """Extract entities using LLM (slower, more accurate)."""
        prompt = f"""Extract entities from the following text. Return a JSON array of objects with "name" and "type" fields.
Types can be: person, organization, technology, concept, location, project, tool, preference.

Text: {content}

Return only valid JSON array, no explanation:"""

        try:
            response = self.llm.generate(prompt)
            # Try to parse JSON from response
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                entity_data = json.loads(json_match.group())
                entities = []
                for item in entity_data:
                    name = item.get("name", "").strip()
                    if name:
                        entity_type = EntityType(item.get("type", "unknown"))
                        entity = self._get_or_create_entity(name, entity_type)
                        entity.memory_ids.add(memory_id)
                        entities.append(entity)

                self.memory_entities[memory_id] = {e.name for e in entities}
                return entities
        except Exception as e:
            logger.warning(f"LLM entity extraction failed: {e}, falling back to regex")

        return self._extract_entities_regex(content, memory_id)

    def _get_or_create_entity(self, name: str, entity_type: EntityType) -> Entity:
        """Get existing entity or create new one."""
        name_lower = name.lower()

        # Check if entity exists (case-insensitive)
        for existing_name, entity in self.entities.items():
            if existing_name.lower() == name_lower or name_lower in {a.lower() for a in entity.aliases}:
                # Update type if we have more specific info
                if entity.entity_type == EntityType.UNKNOWN and entity_type != EntityType.UNKNOWN:
                    entity.entity_type = entity_type
                entity.aliases.add(name)
                return entity

        # Create new entity
        entity = Entity(name=name, entity_type=entity_type)
        self.entities[name] = entity
        return entity

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relation_type: RelationType,
        entity: Optional[str] = None,
        weight: float = 1.0,
    ) -> Relationship:
        """Add a relationship between two memories.

        Args:
            source_id: Source memory ID
            target_id: Target memory ID
            relation_type: Type of relationship
            entity: Optional entity that connects them
            weight: Relationship strength (0-1)

        Returns:
            The created relationship
        """
        rel = Relationship(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            entity=entity,
            weight=weight,
        )
        self.relationships.append(rel)

        # Index by memory ID
        if source_id not in self.memory_relations:
            self.memory_relations[source_id] = []
        self.memory_relations[source_id].append(rel)

        if target_id not in self.memory_relations:
            self.memory_relations[target_id] = []
        self.memory_relations[target_id].append(rel)

        return rel

    def link_by_shared_entities(self, memory_id: str) -> List[Relationship]:
        """Create relationships based on shared entities with other memories.

        Args:
            memory_id: Memory ID to link

        Returns:
            List of created relationships
        """
        if memory_id not in self.memory_entities:
            return []

        my_entities = self.memory_entities[memory_id]
        relationships = []

        for entity_name in my_entities:
            entity = self.entities.get(entity_name)
            if not entity:
                continue

            # Link to other memories that share this entity
            for other_id in entity.memory_ids:
                if other_id != memory_id:
                    # Check if relationship already exists
                    existing = any(
                        r for r in self.relationships
                        if (r.source_id == memory_id and r.target_id == other_id) or
                           (r.source_id == other_id and r.target_id == memory_id)
                    )
                    if not existing:
                        rel = self.add_relationship(
                            source_id=memory_id,
                            target_id=other_id,
                            relation_type=RelationType.SHARED_ENTITY,
                            entity=entity_name,
                        )
                        relationships.append(rel)

        return relationships

    def get_related_memories(
        self,
        memory_id: str,
        max_depth: int = 2,
        relation_types: Optional[List[RelationType]] = None,
    ) -> List[Tuple[str, int, List[Relationship]]]:
        """Get memories related to the given memory via graph traversal.

        Args:
            memory_id: Starting memory ID
            max_depth: Maximum traversal depth
            relation_types: Filter by relationship types (None = all)

        Returns:
            List of (memory_id, depth, path) tuples
        """
        visited = {memory_id}
        results = []
        # Use deque for O(1) popleft instead of list.pop(0) which is O(n).
        queue = deque([(memory_id, 0, [])])

        while queue:
            current_id, depth, path = queue.popleft()

            if depth >= max_depth:
                continue

            for rel in self.memory_relations.get(current_id, []):
                # Filter by relation type
                if relation_types and rel.relation_type not in relation_types:
                    continue

                # Get the other memory in the relationship
                other_id = rel.target_id if rel.source_id == current_id else rel.source_id

                if other_id not in visited:
                    visited.add(other_id)
                    new_path = path + [rel]
                    results.append((other_id, depth + 1, new_path))
                    queue.append((other_id, depth + 1, new_path))

        return results

    def get_entity_memories(self, entity_name: str) -> Set[str]:
        """Get all memory IDs that contain a given entity."""
        entity = self.entities.get(entity_name)
        if entity:
            return entity.memory_ids.copy()

        # Try case-insensitive match
        entity_lower = entity_name.lower()
        for name, ent in self.entities.items():
            if name.lower() == entity_lower:
                return ent.memory_ids.copy()

        return set()

    def get_memory_graph(self, memory_id: str) -> Dict[str, Any]:
        """Get graph data centered on a memory.

        Args:
            memory_id: Center memory ID

        Returns:
            Dict with nodes and edges for visualization
        """
        related = self.get_related_memories(memory_id, max_depth=2)

        nodes = [{"id": memory_id, "type": "memory", "depth": 0}]
        edges = []
        seen_edges = set()

        for other_id, depth, path in related:
            nodes.append({"id": other_id, "type": "memory", "depth": depth})
            for rel in path:
                edge_key = (rel.source_id, rel.target_id, rel.relation_type.value)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": rel.source_id,
                        "target": rel.target_id,
                        "type": rel.relation_type.value,
                        "entity": rel.entity,
                        "weight": rel.weight,
                    })

        # Add entity nodes
        for entity_name in self.memory_entities.get(memory_id, []):
            entity = self.entities.get(entity_name)
            if entity:
                nodes.append({
                    "id": f"entity:{entity_name}",
                    "type": "entity",
                    "entity_type": entity.entity_type.value,
                    "name": entity_name,
                })
                edges.append({
                    "source": memory_id,
                    "target": f"entity:{entity_name}",
                    "type": "has_entity",
                })

        return {"nodes": nodes, "edges": edges}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize graph to dictionary."""
        return {
            "entities": {name: e.to_dict() for name, e in self.entities.items()},
            "relationships": [r.to_dict() for r in self.relationships],
            "memory_entities": {k: list(v) for k, v in self.memory_entities.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], llm=None) -> "KnowledgeGraph":
        """Deserialize graph from dictionary."""
        graph = cls(llm=llm)

        for name, entity_data in data.get("entities", {}).items():
            graph.entities[name] = Entity.from_dict(entity_data)

        for rel_data in data.get("relationships", []):
            rel = Relationship.from_dict(rel_data)
            graph.relationships.append(rel)

            # Rebuild memory_relations index
            if rel.source_id not in graph.memory_relations:
                graph.memory_relations[rel.source_id] = []
            graph.memory_relations[rel.source_id].append(rel)

            if rel.target_id not in graph.memory_relations:
                graph.memory_relations[rel.target_id] = []
            graph.memory_relations[rel.target_id].append(rel)

        graph.memory_entities = {
            k: set(v) for k, v in data.get("memory_entities", {}).items()
        }

        return graph

    def stats(self) -> Dict[str, Any]:
        """Get graph statistics."""
        return {
            "total_entities": len(self.entities),
            "total_relationships": len(self.relationships),
            "total_memories_indexed": len(self.memory_entities),
            "entity_types": {
                t.value: sum(1 for e in self.entities.values() if e.entity_type == t)
                for t in EntityType
            },
            "relationship_types": {
                t.value: sum(1 for r in self.relationships if r.relation_type == t)
                for t in RelationType
            },
        }
