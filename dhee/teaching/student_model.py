"""StudentModel — per-student profile and concept mastery via Engram.

Profiles are stored as ``memory_type="student_profile"`` and per-concept
mastery as ``memory_type="concept_mastery"``. FadeMem handles mastery
decay naturally: accessing a concept mastery memory boosts its strength
(spaced repetition).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dhee.teaching.config import TeachingConfig

logger = logging.getLogger(__name__)


class StudentModel:
    """Per-student learning profile and concept mastery tracker."""

    def __init__(self, memory: "CoreMemory", config: TeachingConfig | None = None):  # noqa: F821
        self.memory = memory
        self.config = config or TeachingConfig()

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def get_or_create_profile(self, student_id: str) -> Dict[str, Any]:
        """Get or create a student learning profile."""
        existing = self._find_profile(student_id)
        if existing:
            return existing

        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "memory_type": "student_profile",
            "student_id": student_id,
            "learning_style": "unknown",
            "interests": [],
            "goals": [],
            "effective_analogies": [],
            "created_at": now,
            "updated_at": now,
        }

        content = f"Student profile for {student_id}"
        result = self.memory.add(
            content=content,
            user_id=student_id,
            metadata=meta,
            categories=["student/profile"],
        )

        mem_id = self._extract_id(result)
        return {
            "memory_id": mem_id,
            "student_id": student_id,
            "learning_style": "unknown",
            "interests": [],
            "goals": [],
            "effective_analogies": [],
        }

    def update_profile(
        self,
        student_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge updates into the student profile."""
        profile_mem = self._find_profile_raw(student_id)
        if not profile_mem:
            profile = self.get_or_create_profile(student_id)
            profile_mem = self._find_profile_raw(student_id)
            if not profile_mem:
                return profile

        md = self._parse_metadata(profile_mem)
        mem_id = profile_mem.get("id")

        # Merge updates
        for key, value in updates.items():
            if key in ("interests", "goals", "effective_analogies") and isinstance(value, list):
                existing = md.get(key, [])
                merged = list(dict.fromkeys(existing + value))  # dedup, preserve order
                md[key] = merged
            else:
                md[key] = value

        md["updated_at"] = datetime.now(timezone.utc).isoformat()

        if hasattr(self.memory, "db") and hasattr(self.memory.db, "update_memory"):
            self.memory.db.update_memory(mem_id, {"metadata": json.dumps(md)})

        return self._format_profile({"id": mem_id, "metadata": md})

    # ------------------------------------------------------------------
    # Mastery
    # ------------------------------------------------------------------

    def get_mastery(self, student_id: str, concept_id: str) -> float:
        """Get current mastery score. Accessing boosts strength (spaced repetition)."""
        mastery_mem = self._find_mastery_raw(student_id, concept_id)
        if not mastery_mem:
            return self.config.mastery_initial_score

        md = self._parse_metadata(mastery_mem)
        score = md.get("mastery_score", self.config.mastery_initial_score)

        # Access the memory to trigger FadeMem strength boost
        mem_id = mastery_mem.get("id")
        if mem_id and hasattr(self.memory, "db"):
            self.memory.db.increment_access(mem_id)

        return score

    def update_mastery(
        self,
        student_id: str,
        concept_id: str,
        score_delta: float,
    ) -> float:
        """Update mastery score by delta. Returns new score."""
        mastery_mem = self._find_mastery_raw(student_id, concept_id)

        if not mastery_mem:
            # Create new mastery record
            new_score = max(0.0, min(1.0, self.config.mastery_initial_score + score_delta))
            now = datetime.now(timezone.utc).isoformat()
            meta = {
                "memory_type": "concept_mastery",
                "student_id": student_id,
                "concept_id": concept_id,
                "mastery_score": new_score,
                "history": [{"delta": score_delta, "score": new_score, "at": now}],
                "created_at": now,
                "updated_at": now,
            }
            self.memory.add(
                content=f"Mastery: {student_id} / {concept_id} = {new_score:.2f}",
                user_id=student_id,
                metadata=meta,
                categories=["student/mastery"],
            )
            return new_score

        md = self._parse_metadata(mastery_mem)
        old_score = md.get("mastery_score", self.config.mastery_initial_score)
        new_score = max(0.0, min(1.0, old_score + score_delta))

        now = datetime.now(timezone.utc).isoformat()
        history = md.get("history", [])
        history.append({"delta": score_delta, "score": new_score, "at": now})
        md["mastery_score"] = new_score
        md["history"] = history[-50:]  # keep last 50 entries
        md["updated_at"] = now

        mem_id = mastery_mem.get("id")
        if mem_id and hasattr(self.memory, "db"):
            self.memory.db.update_memory(mem_id, {"metadata": json.dumps(md)})
            self.memory.db.increment_access(mem_id)

        return new_score

    def get_weak_concepts(
        self,
        student_id: str,
        threshold: float | None = None,
    ) -> List[Dict[str, Any]]:
        """Get concepts below the weak threshold."""
        threshold = threshold or self.config.weak_concept_threshold
        all_mastery = self._get_all_mastery(student_id)
        return [
            m for m in all_mastery
            if m["mastery_score"] < threshold
        ]

    def get_decay_risk_concepts(self, student_id: str) -> List[Dict[str, Any]]:
        """Get concepts whose mastery is decaying below the promotion threshold."""
        all_mastery = self._get_all_mastery(student_id)
        results = []
        for m in all_mastery:
            mem = self._find_mastery_raw(student_id, m["concept_id"])
            if not mem:
                continue
            # Check if Engram strength is decaying
            strength = mem.get("strength", 1.0)
            if strength < 0.5 and m["mastery_score"] >= self.config.weak_concept_threshold:
                results.append({**m, "decay_strength": strength})
        return results

    def record_misconception(
        self,
        student_id: str,
        concept_id: str,
        misconception: str,
        correction: str,
    ) -> None:
        """Record a misconception and apply mastery penalty."""
        self.update_mastery(
            student_id, concept_id, self.config.mastery_decrement_on_misconception
        )

        meta = {
            "memory_type": "misconception",
            "student_id": student_id,
            "concept_id": concept_id,
            "misconception": misconception,
            "correction": correction,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.memory.add(
            content=f"Misconception ({concept_id}): {misconception} -> Correction: {correction}",
            user_id=student_id,
            metadata=meta,
            categories=["student/misconception"],
        )

    def record_effective_analogy(
        self,
        student_id: str,
        concept_id: str,
        analogy: str,
        success: bool = True,
    ) -> None:
        """Record an analogy that worked (or didn't) for a student."""
        meta = {
            "memory_type": "effective_analogy",
            "student_id": student_id,
            "concept_id": concept_id,
            "analogy": analogy,
            "success": success,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.memory.add(
            content=f"Analogy ({concept_id}): {analogy} [{'success' if success else 'failed'}]",
            user_id=student_id,
            metadata=meta,
            categories=["student/analogy"],
        )

        # Also update profile with effective analogies
        if success:
            self.update_profile(student_id, {
                "effective_analogies": [{"concept_id": concept_id, "analogy": analogy}],
            })

    def get_effective_analogies(
        self,
        student_id: str,
        concept_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve effective analogies for a student, optionally filtered by concept."""
        if not hasattr(self.memory, "db"):
            return []

        memories = self.memory.db.get_all_memories(
            user_id=student_id,
            memory_type="effective_analogy",
            limit=100,
        )

        results = []
        for mem in memories:
            md = self._parse_metadata(mem)
            if md.get("memory_type") != "effective_analogy":
                continue
            if not md.get("success", True):
                continue
            if concept_id and md.get("concept_id") != concept_id:
                continue
            results.append({
                "concept_id": md.get("concept_id", ""),
                "analogy": md.get("analogy", ""),
                "success": md.get("success", True),
            })
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_profile(self, student_id: str) -> Optional[Dict[str, Any]]:
        raw = self._find_profile_raw(student_id)
        return self._format_profile(raw) if raw else None

    def _find_profile_raw(self, student_id: str) -> Optional[Dict[str, Any]]:
        if not hasattr(self.memory, "db"):
            return None
        memories = self.memory.db.get_all_memories(
            user_id=student_id,
            memory_type="student_profile",
            limit=5,
        )
        for mem in memories:
            md = self._parse_metadata(mem)
            if md.get("memory_type") == "student_profile":
                return mem
        return None

    def _find_mastery_raw(
        self, student_id: str, concept_id: str
    ) -> Optional[Dict[str, Any]]:
        if not hasattr(self.memory, "db"):
            return None
        memories = self.memory.db.get_all_memories(
            user_id=student_id,
            memory_type="concept_mastery",
            limit=500,
        )
        for mem in memories:
            md = self._parse_metadata(mem)
            if (
                md.get("memory_type") == "concept_mastery"
                and md.get("concept_id") == concept_id
            ):
                return mem
        return None

    def _get_all_mastery(self, student_id: str) -> List[Dict[str, Any]]:
        if not hasattr(self.memory, "db"):
            return []
        memories = self.memory.db.get_all_memories(
            user_id=student_id,
            memory_type="concept_mastery",
            limit=500,
        )
        results = []
        for mem in memories:
            md = self._parse_metadata(mem)
            if md.get("memory_type") == "concept_mastery":
                results.append({
                    "concept_id": md.get("concept_id", ""),
                    "mastery_score": md.get("mastery_score", self.config.mastery_initial_score),
                    "memory_id": mem.get("id"),
                })
        return results

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
    def _format_profile(cls, mem: Dict[str, Any]) -> Dict[str, Any]:
        md = cls._parse_metadata(mem)
        return {
            "memory_id": mem.get("id"),
            "student_id": md.get("student_id", ""),
            "learning_style": md.get("learning_style", "unknown"),
            "interests": md.get("interests", []),
            "goals": md.get("goals", []),
            "effective_analogies": md.get("effective_analogies", []),
        }
