"""TeachingMemory — specialized memory types for the teaching domain.

Stores lesson episodes, concept explanations, comprehension checks,
misconceptions, and effective analogies as Engram memories.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dhee.teaching.config import TeachingConfig

logger = logging.getLogger(__name__)


class TeachingMemory:
    """Domain-specific memory storage for teaching interactions."""

    def __init__(self, memory: "CoreMemory", config: TeachingConfig | None = None):  # noqa: F821
        self.memory = memory
        self.config = config or TeachingConfig()

    # ------------------------------------------------------------------
    # Lesson episodes
    # ------------------------------------------------------------------

    def store_lesson_episode(
        self,
        student_id: str,
        concept_id: str,
        lesson_summary: str,
        *,
        segments_completed: int = 0,
        session_id: str = "",
        topic_name: str = "",
        subject: str = "",
    ) -> Dict[str, Any]:
        """Store a completed (or partial) lesson episode."""
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "memory_type": "lesson_episode",
            "student_id": student_id,
            "concept_id": concept_id,
            "session_id": session_id,
            "segments_completed": segments_completed,
            "topic_name": topic_name,
            "subject": subject,
            "recorded_at": now,
        }

        content = f"Lesson ({topic_name or concept_id}): {lesson_summary}"
        result = self.memory.add(
            content=content,
            user_id=student_id,
            metadata=meta,
            categories=[f"lesson/{subject}" if subject else "lesson"],
        )

        mem_id = self._extract_id(result)
        return {"memory_id": mem_id, **meta}

    # ------------------------------------------------------------------
    # Concept explanations
    # ------------------------------------------------------------------

    def store_concept_explanation(
        self,
        student_id: str,
        concept_id: str,
        approach: str,
        explanation_text: str,
        success: bool = True,
    ) -> Dict[str, Any]:
        """Store an explanation attempt for a concept."""
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "memory_type": "concept_explanation",
            "student_id": student_id,
            "concept_id": concept_id,
            "approach": approach,
            "success": success,
            "recorded_at": now,
        }

        content = f"Explanation ({concept_id}, {approach}): {explanation_text[:500]}"
        result = self.memory.add(
            content=content,
            user_id=student_id,
            metadata=meta,
            categories=["teaching/explanation"],
        )

        mem_id = self._extract_id(result)
        return {"memory_id": mem_id, **meta}

    # ------------------------------------------------------------------
    # Comprehension checks
    # ------------------------------------------------------------------

    def store_comprehension_check(
        self,
        student_id: str,
        concept_id: str,
        question: str,
        student_answer: str,
        evaluation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Store a comprehension check result."""
        now = datetime.now(timezone.utc).isoformat()
        level = evaluation.get("level", "unknown")
        meta = {
            "memory_type": "comprehension_check",
            "student_id": student_id,
            "concept_id": concept_id,
            "question": question,
            "student_answer": student_answer,
            "level": level,
            "confidence": evaluation.get("confidence", 0.5),
            "misconception": evaluation.get("misconception"),
            "recorded_at": now,
        }

        content = (
            f"Check ({concept_id}): Q: {question[:200]} | "
            f"A: {student_answer[:200]} | Level: {level}"
        )
        result = self.memory.add(
            content=content,
            user_id=student_id,
            metadata=meta,
            categories=["teaching/check"],
        )

        mem_id = self._extract_id(result)
        return {"memory_id": mem_id, **meta}

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_past_explanations(
        self,
        student_id: str,
        concept_id: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Find past explanations for a concept with a specific student."""
        results = self.memory.search(
            query=f"explanation {concept_id}",
            user_id=student_id,
            limit=limit * 2,
        )

        explanations = []
        for mem in results:
            md = self._parse_metadata(mem)
            if (
                md.get("memory_type") == "concept_explanation"
                and md.get("concept_id") == concept_id
            ):
                explanations.append({
                    "memory_id": mem.get("id"),
                    "approach": md.get("approach", ""),
                    "success": md.get("success", True),
                    "content": mem.get("content", ""),
                    "strength": mem.get("strength", 1.0),
                })
            if len(explanations) >= limit:
                break

        return explanations

    def search_student_history(
        self,
        student_id: str,
        query: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search all teaching memories for a student (grounding context)."""
        results = self.memory.search(
            query=query,
            user_id=student_id,
            limit=limit,
        )

        return [
            {
                "memory_id": mem.get("id"),
                "content": mem.get("content", ""),
                "memory_type": self._parse_metadata(mem).get("memory_type", ""),
                "strength": mem.get("strength", 1.0),
            }
            for mem in results
        ]

    def get_student_misconceptions(
        self,
        student_id: str,
        concept_id: str | None = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Retrieve recorded misconceptions for a student."""
        if not hasattr(self.memory, "db"):
            return []

        memories = self.memory.db.get_all_memories(
            user_id=student_id,
            memory_type="misconception",
            limit=limit * 2,
        )

        results = []
        for mem in memories:
            md = self._parse_metadata(mem)
            if md.get("memory_type") != "misconception":
                continue
            if concept_id and md.get("concept_id") != concept_id:
                continue
            results.append({
                "concept_id": md.get("concept_id", ""),
                "misconception": md.get("misconception", ""),
                "correction": md.get("correction", ""),
                "recorded_at": md.get("recorded_at", ""),
            })
            if len(results) >= limit:
                break

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
