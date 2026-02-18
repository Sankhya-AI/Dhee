"""SkillStore — filesystem + vector index for skills.

Skills are stored as {skill_id}.skill.md files and indexed in a vector
store collection for semantic discovery.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from engram.skills.schema import Skill

logger = logging.getLogger(__name__)


class SkillStore:
    """Manages skill persistence on filesystem with vector indexing."""

    def __init__(
        self,
        skill_dirs: List[str],
        embedder: Any = None,
        vector_store: Any = None,
        collection_name: str = "engram_skills",
    ):
        self._skill_dirs = skill_dirs
        self._embedder = embedder
        self._vector_store = vector_store
        self._collection_name = collection_name
        self._cache: Dict[str, Skill] = {}

        # Ensure skill directories exist
        for d in self._skill_dirs:
            os.makedirs(d, exist_ok=True)

    @property
    def primary_dir(self) -> str:
        """Primary directory for saving new skills."""
        return self._skill_dirs[0] if self._skill_dirs else os.path.expanduser("~/.engram/skills")

    def save(self, skill: Skill) -> str:
        """Write skill to filesystem and upsert into vector index."""
        filename = f"{skill.id}.skill.md"
        filepath = os.path.join(self.primary_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        content = skill.to_skill_md()
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        self._cache[skill.id] = skill

        # Index in vector store
        if self._embedder and self._vector_store:
            try:
                text = f"{skill.name}. {skill.description}"
                embedding = self._embedder.embed(text, memory_action="add")
                self._vector_store.insert(
                    vectors=[embedding],
                    payloads=[{
                        "skill_id": skill.id,
                        "name": skill.name,
                        "description": skill.description,
                        "tags": ",".join(skill.tags),
                        "confidence": skill.confidence,
                        "user_id": "system",
                    }],
                    ids=[skill.id],
                )
            except Exception as e:
                logger.warning("Failed to index skill %s: %s", skill.id, e)

        return skill.id

    def get(self, skill_id: str) -> Optional[Skill]:
        """Get a skill by ID (cache → filesystem)."""
        if skill_id in self._cache:
            return self._cache[skill_id]

        # Search filesystem
        for d in self._skill_dirs:
            filepath = os.path.join(d, f"{skill_id}.skill.md")
            if os.path.isfile(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        skill = Skill.from_skill_md(f.read())
                    self._cache[skill.id] = skill
                    return skill
                except Exception as e:
                    logger.warning("Failed to load skill %s: %s", filepath, e)
        return None

    def search(
        self,
        query: str,
        limit: int = 5,
        tags: Optional[List[str]] = None,
        min_confidence: float = 0.0,
    ) -> List[Skill]:
        """Semantic search over indexed skills."""
        if not self._embedder or not self._vector_store:
            # Fallback: simple text matching over cached skills
            return self._text_search(query, limit, tags, min_confidence)

        try:
            embedding = self._embedder.embed(query, memory_action="search")
            results = self._vector_store.search(
                query=None,
                vectors=embedding,
                limit=limit * 2,
            )
        except Exception as e:
            logger.warning("Skill vector search failed: %s", e)
            return self._text_search(query, limit, tags, min_confidence)

        skills = []
        for r in results:
            payload = r.payload if hasattr(r, "payload") else r.get("payload", {})
            skill_id = payload.get("skill_id", r.id if hasattr(r, "id") else "")
            skill = self.get(skill_id)
            if skill is None:
                continue
            if skill.confidence < min_confidence:
                continue
            if tags and not any(t in skill.tags for t in tags):
                continue
            skills.append(skill)
            if len(skills) >= limit:
                break

        return skills

    def _text_search(
        self,
        query: str,
        limit: int,
        tags: Optional[List[str]],
        min_confidence: float,
    ) -> List[Skill]:
        """Simple text matching fallback."""
        query_lower = query.lower()
        matches = []
        for skill in self._cache.values():
            if skill.confidence < min_confidence:
                continue
            if tags and not any(t in skill.tags for t in tags):
                continue
            text = f"{skill.name} {skill.description} {' '.join(skill.tags)}".lower()
            if any(word in text for word in query_lower.split()):
                matches.append(skill)
        return matches[:limit]

    def get_by_signature(self, sig_hash: str) -> Optional[Skill]:
        """Find skill by signature hash (dedup check)."""
        for skill in self._cache.values():
            if skill.signature_hash == sig_hash:
                return skill

        # Also check filesystem
        self.sync_from_filesystem()
        for skill in self._cache.values():
            if skill.signature_hash == sig_hash:
                return skill
        return None

    def search_structural(
        self,
        query_steps: List[str],
        limit: int = 5,
        min_similarity: float = 0.3,
    ) -> List[Skill]:
        """Search for skills by structural similarity to given steps."""
        from engram.skills.structure import (
            extract_slots_heuristic,
            structural_similarity,
        )

        _, query_structured = extract_slots_heuristic(query_steps)

        scored: List[tuple] = []
        for skill in self._cache.values():
            structure = skill.get_structure()
            if structure is None:
                continue
            sim = structural_similarity(query_structured, structure.structured_steps)
            if sim >= min_similarity:
                scored.append((sim, skill))

        # Also check filesystem for skills not yet cached
        self.sync_from_filesystem()
        for skill in self._cache.values():
            structure = skill.get_structure()
            if structure is None:
                continue
            # Avoid re-scoring already scored skills
            if any(s.id == skill.id for _, s in scored):
                continue
            sim = structural_similarity(query_structured, structure.structured_steps)
            if sim >= min_similarity:
                scored.append((sim, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [skill for _, skill in scored[:limit]]

    def get_by_structural_signature(self, sig_hash: str) -> Optional[Skill]:
        """Find skill by structural signature hash (structural dedup check)."""
        for skill in self._cache.values():
            structure = skill.get_structure()
            if structure and structure.structural_signature == sig_hash:
                return skill

        self.sync_from_filesystem()
        for skill in self._cache.values():
            structure = skill.get_structure()
            if structure and structure.structural_signature == sig_hash:
                return skill
        return None

    def delete(self, skill_id: str) -> bool:
        """Delete a skill from filesystem and index."""
        self._cache.pop(skill_id, None)

        for d in self._skill_dirs:
            filepath = os.path.join(d, f"{skill_id}.skill.md")
            if os.path.isfile(filepath):
                os.remove(filepath)

        if self._vector_store:
            try:
                self._vector_store.delete(skill_id)
            except Exception:
                pass
        return True

    def list_all(self) -> List[Skill]:
        """List all cached skills."""
        return list(self._cache.values())

    def sync_from_filesystem(self) -> int:
        """Scan skill directories and index any unindexed SKILL.md files.

        Returns count of newly indexed skills.
        """
        count = 0
        for d in self._skill_dirs:
            if not os.path.isdir(d):
                continue
            for filename in os.listdir(d):
                if not filename.endswith(".skill.md"):
                    continue
                skill_id = filename.replace(".skill.md", "")
                if skill_id in self._cache:
                    continue
                filepath = os.path.join(d, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        skill = Skill.from_skill_md(f.read())
                    self._cache[skill.id] = skill

                    # Index in vector store
                    if self._embedder and self._vector_store:
                        text = f"{skill.name}. {skill.description}"
                        embedding = self._embedder.embed(text, memory_action="add")
                        self._vector_store.insert(
                            vectors=[embedding],
                            payloads=[{
                                "skill_id": skill.id,
                                "name": skill.name,
                                "description": skill.description,
                                "tags": ",".join(skill.tags),
                                "confidence": skill.confidence,
                                "user_id": "system",
                            }],
                            ids=[skill.id],
                        )
                    count += 1
                except Exception as e:
                    logger.warning("Failed to sync skill %s: %s", filepath, e)
        return count
