"""ConceptStore — curriculum concepts stored as Engram memories.

Concepts are stored with ``memory_type="concept"`` under a shared
``user_id`` (the curriculum namespace). Prerequisites and cross-subject
links are represented via the knowledge graph (``RelationType.REQUIRES``
and ``RelationType.RELATED_TO``).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from dhee.teaching.config import TeachingConfig

logger = logging.getLogger(__name__)


class ConceptStore:
    """CRUD and graph operations over curriculum concepts."""

    def __init__(self, memory: "CoreMemory", config: TeachingConfig | None = None):  # noqa: F821
        self.memory = memory
        self.config = config or TeachingConfig()
        self._namespace = self.config.concept_namespace

    # ------------------------------------------------------------------
    # Create / update
    # ------------------------------------------------------------------

    def add_concept(
        self,
        concept_id: str,
        name: str,
        subject: str,
        *,
        difficulty: float = 0.5,
        prerequisites: List[str] | None = None,
        cross_subject_links: List[str] | None = None,
        keywords: List[str] | None = None,
        description: str = "",
    ) -> Dict[str, Any]:
        """Add a concept to the store (idempotent by ``concept_id``)."""

        # Check for existing concept with same concept_id
        existing = self._find_by_concept_id(concept_id)
        if existing:
            return existing

        meta = {
            "memory_type": "concept",
            "concept_id": concept_id,
            "subject": subject,
            "difficulty": difficulty,
            "prerequisites": prerequisites or [],
            "cross_subject_links": cross_subject_links or [],
            "keywords": keywords or [],
        }

        content = f"{name}: {description}" if description else name
        result = self.memory.add(
            content=content,
            user_id=self._namespace,
            metadata=meta,
            categories=[f"concept/{subject}"],
        )

        mem_id = self._extract_id(result)
        if not mem_id:
            logger.warning("Failed to store concept %s", concept_id)
            return {"error": "Failed to store concept"}

        # Create prerequisite edges via knowledge graph
        if prerequisites and hasattr(self.memory, "knowledge_graph"):
            graph = self.memory.knowledge_graph
            for prereq_id in prerequisites:
                prereq = self._find_by_concept_id(prereq_id)
                prereq_mem_id = prereq.get("memory_id") if prereq else None
                if prereq_mem_id:
                    from dhee.core.graph import RelationType

                    graph.add_relationship(
                        source_id=mem_id,
                        target_id=prereq_mem_id,
                        relation_type=RelationType.REQUIRES,
                        metadata={"concept_link": True},
                    )

        # Create cross-subject edges
        if cross_subject_links and hasattr(self.memory, "knowledge_graph"):
            graph = self.memory.knowledge_graph
            for linked_id in cross_subject_links:
                linked = self._find_by_concept_id(linked_id)
                linked_mem_id = linked.get("memory_id") if linked else None
                if linked_mem_id:
                    from dhee.core.graph import RelationType

                    graph.add_relationship(
                        source_id=mem_id,
                        target_id=linked_mem_id,
                        relation_type=RelationType.RELATED_TO,
                        metadata={"cross_subject": True},
                    )

        return {
            "memory_id": mem_id,
            "concept_id": concept_id,
            "name": name,
            "subject": subject,
        }

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_concept(self, concept_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a concept by its concept_id."""
        return self._find_by_concept_id(concept_id)

    def search_concepts(
        self,
        query: str,
        subject: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Semantic search over concepts, optionally filtered by subject."""
        results = self.memory.search(
            query=query,
            user_id=self._namespace,
            limit=limit * 2,
        )

        concepts = []
        for mem in results:
            md = self._parse_metadata(mem)
            if md.get("memory_type") != "concept":
                continue
            if subject and md.get("subject") != subject:
                continue
            concepts.append(self._format_concept(mem))
            if len(concepts) >= limit:
                break

        return concepts

    def get_prerequisites(
        self,
        concept_id: str,
        depth: int = 2,
    ) -> List[Dict[str, Any]]:
        """Traverse prerequisite graph to the given depth."""
        root = self._find_by_concept_id(concept_id)
        if not root:
            return []

        root_mem_id = root.get("memory_id")
        if not root_mem_id:
            return []

        # Try graph traversal first
        if hasattr(self.memory, "knowledge_graph"):
            from dhee.core.graph import RelationType

            graph = self.memory.knowledge_graph
            related = graph.get_related_memories(
                root_mem_id,
                relation_types=[RelationType.REQUIRES],
                max_depth=depth,
            )
            prereqs = []
            for rel in related:
                target_id = rel.get("target_id") or rel.get("memory_id")
                if target_id:
                    mem = self.memory.get(target_id)
                    if mem:
                        prereqs.append(self._format_concept(mem))
            return prereqs

        # Fallback: use metadata-based prerequisites
        md = self._parse_metadata(root)
        prereq_ids = md.get("prerequisites", [])
        prereqs = []
        for pid in prereq_ids:
            c = self._find_by_concept_id(pid)
            if c:
                prereqs.append(c)
        return prereqs

    def get_cross_subject_links(
        self,
        concept_id: str,
    ) -> List[Dict[str, Any]]:
        """Find concepts linked across subjects."""
        root = self._find_by_concept_id(concept_id)
        if not root:
            return []

        root_mem_id = root.get("memory_id")
        if not root_mem_id:
            return []

        if hasattr(self.memory, "knowledge_graph"):
            from dhee.core.graph import RelationType

            graph = self.memory.knowledge_graph
            related = graph.get_related_memories(
                root_mem_id,
                relation_types=[RelationType.RELATED_TO],
                max_depth=1,
            )
            links = []
            for rel in related:
                target_id = rel.get("target_id") or rel.get("memory_id")
                if target_id:
                    mem = self.memory.get(target_id)
                    if mem:
                        links.append(self._format_concept(mem))
            return links

        # Fallback
        md = self._parse_metadata(root)
        linked_ids = md.get("cross_subject_links", [])
        return [c for pid in linked_ids if (c := self._find_by_concept_id(pid))]

    def link_concepts_cross_subject(
        self,
        concept_a_id: str,
        concept_b_id: str,
    ) -> bool:
        """Create a cross-subject edge between two concepts."""
        a = self._find_by_concept_id(concept_a_id)
        b = self._find_by_concept_id(concept_b_id)
        if not a or not b:
            return False

        a_mem_id = a.get("memory_id")
        b_mem_id = b.get("memory_id")
        if not a_mem_id or not b_mem_id:
            return False

        if hasattr(self.memory, "knowledge_graph"):
            from dhee.core.graph import RelationType

            self.memory.knowledge_graph.add_relationship(
                source_id=a_mem_id,
                target_id=b_mem_id,
                relation_type=RelationType.RELATED_TO,
                metadata={"cross_subject": True},
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_by_concept_id(self, concept_id: str) -> Optional[Dict[str, Any]]:
        """Find concept memory by concept_id in metadata."""
        if hasattr(self.memory, "db") and hasattr(self.memory.db, "get_all_memories"):
            memories = self.memory.db.get_all_memories(
                user_id=self._namespace,
                memory_type="concept",
                limit=500,
            )
            for mem in memories:
                md = self._parse_metadata(mem)
                if md.get("concept_id") == concept_id:
                    return self._format_concept(mem)
        return None

    @staticmethod
    def _extract_id(result: Dict[str, Any]) -> Optional[str]:
        if isinstance(result, dict):
            results = result.get("results", [])
            if results and isinstance(results, list):
                first = results[0]
                return first.get("id") or first.get("memory_id")
            return result.get("id") or result.get("memory_id")
        return None

    @staticmethod
    def _parse_metadata(mem: Dict[str, Any]) -> Dict[str, Any]:
        md = mem.get("metadata", {})
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except (json.JSONDecodeError, TypeError):
                md = {}
        return md

    @classmethod
    def _format_concept(cls, mem: Dict[str, Any]) -> Dict[str, Any]:
        md = cls._parse_metadata(mem)
        return {
            "memory_id": mem.get("id"),
            "concept_id": md.get("concept_id", ""),
            "name": mem.get("content", "").split(":")[0].strip(),
            "subject": md.get("subject", ""),
            "difficulty": md.get("difficulty", 0.5),
            "prerequisites": md.get("prerequisites", []),
            "cross_subject_links": md.get("cross_subject_links", []),
            "keywords": md.get("keywords", []),
            "strength": mem.get("strength", 1.0),
        }
