"""Heuristic Distillation — abstract transferable reasoning patterns.

Based on ERL (arXiv:2603.24639): distill trajectories into abstract heuristics,
not raw logs. +7.8% over baselines by learning transferable patterns.

The key insight: raw trajectory logs are too specific to transfer across tasks.
Abstract heuristics like "decompose auth problems into token lifecycle stages"
transfer where "fixed JWT refresh in auth.py line 42" does not.

Three abstraction levels:
  specific  — "When JWT tokens expire, check refresh logic first"
  domain    — "For authentication bugs, trace the token lifecycle"
  universal — "When debugging, start with the most constrained component"

Heuristics are:
  1. Surfaced in HyperContext alongside insights
  2. Used as training data for BuddhiMini's [HEURISTIC] task head
  3. Validated/invalidated by outcomes, like insights
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Heuristic:
    """An abstract, transferable reasoning pattern."""

    id: str
    content: str
    abstraction_level: str          # specific | domain | universal
    source_task_types: List[str]
    confidence: float               # 0-1, updated by outcomes
    created_at: float
    user_id: str = "default"
    validation_count: int = 0
    invalidation_count: int = 0
    tags: List[str] = field(default_factory=list)

    def strength(self) -> float:
        total = self.validation_count + self.invalidation_count
        if total == 0:
            return self.confidence
        return self.confidence * (self.validation_count / total)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "abstraction_level": self.abstraction_level,
            "source_task_types": self.source_task_types,
            "confidence": round(self.confidence, 3),
            "strength": round(self.strength(), 3),
            "created_at": self.created_at,
            "user_id": self.user_id,
            "validation_count": self.validation_count,
            "invalidation_count": self.invalidation_count,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Heuristic:
        return cls(
            id=d["id"],
            content=d["content"],
            abstraction_level=d.get("abstraction_level", "domain"),
            source_task_types=d.get("source_task_types", []),
            confidence=d.get("confidence", 0.5),
            created_at=d.get("created_at", time.time()),
            user_id=d.get("user_id", "default"),
            validation_count=d.get("validation_count", 0),
            invalidation_count=d.get("invalidation_count", 0),
            tags=d.get("tags", []),
        )

    def to_compact(self) -> Dict[str, Any]:
        """Compact format for HyperContext."""
        return {
            "heuristic": self.content[:300],
            "level": self.abstraction_level,
            "confidence": round(self.strength(), 2),
            "applies_to": self.source_task_types[:3],
        }


class HeuristicDistiller:
    """Distills abstract heuristics from trajectories and task outcomes.

    Works in two modes:
      1. With LLM: asks the model to generalize from concrete experiences
      2. Without LLM: extracts patterns heuristically from trajectory structure

    Either way, the output is the same: a Heuristic dataclass stored to disk.
    """

    def __init__(self, data_dir: Optional[str] = None, llm=None):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "heuristics"
        )
        os.makedirs(self._dir, exist_ok=True)
        self._llm = llm
        self._heuristics: Dict[str, Heuristic] = {}
        self._load()

    def distill_from_trajectory(
        self,
        task_description: str,
        task_type: str,
        what_worked: str,
        what_failed: Optional[str] = None,
        user_id: str = "default",
        level: str = "domain",
    ) -> Heuristic:
        """Distill a heuristic from a single trajectory's outcome.

        Called after checkpoint() when what_worked is provided.
        """
        content = self._abstract(task_description, task_type, what_worked, what_failed, level)

        heuristic = Heuristic(
            id=str(uuid.uuid4()),
            content=content,
            abstraction_level=level,
            source_task_types=[task_type],
            confidence=0.6,
            created_at=time.time(),
            user_id=user_id,
            tags=[task_type, level],
        )

        # Deduplicate: if a very similar heuristic exists, boost it instead
        existing = self._find_similar(content, user_id)
        if existing:
            existing.validation_count += 1
            existing.confidence = min(1.0, existing.confidence + 0.05)
            if task_type not in existing.source_task_types:
                existing.source_task_types.append(task_type)
            self._save_all()
            return existing

        self._heuristics[heuristic.id] = heuristic
        self._append(heuristic)
        return heuristic

    def distill_from_cluster(
        self,
        task_descriptions: List[str],
        task_type: str,
        common_patterns: List[str],
        user_id: str = "default",
    ) -> List[Heuristic]:
        """Distill heuristics from a cluster of similar trajectories.

        Called by SkillMiner after clustering successful trajectories.
        Finds what's common across multiple successes → more abstract.
        """
        if not common_patterns:
            return []

        heuristics = []
        for pattern in common_patterns[:5]:
            # Cluster-derived patterns are more reliable
            level = "domain" if len(task_descriptions) >= 3 else "specific"
            h = Heuristic(
                id=str(uuid.uuid4()),
                content=pattern,
                abstraction_level=level,
                source_task_types=[task_type],
                confidence=min(0.9, 0.5 + 0.1 * len(task_descriptions)),
                created_at=time.time(),
                user_id=user_id,
                tags=[task_type, level, "cluster_derived"],
            )

            existing = self._find_similar(pattern, user_id)
            if existing:
                existing.validation_count += 1
                existing.confidence = min(1.0, existing.confidence + 0.05)
                heuristics.append(existing)
            else:
                self._heuristics[h.id] = h
                heuristics.append(h)

        self._save_all()
        return heuristics

    def retrieve_relevant(
        self,
        task_description: str,
        user_id: str = "default",
        limit: int = 5,
    ) -> List[Heuristic]:
        """Find heuristics relevant to a task, sorted by strength."""
        query_words = set(task_description.lower().split())
        if not query_words:
            return []

        scored: List[tuple] = []
        for h in self._heuristics.values():
            if h.user_id != user_id or h.strength() < 0.1:
                continue
            h_words = set(h.content.lower().split())
            h_words |= set(t.lower() for t in h.tags)
            h_words |= set(t.lower() for t in h.source_task_types)
            overlap = len(query_words & h_words)
            if overlap > 0:
                # Higher abstraction = more likely to be relevant
                level_bonus = {"universal": 0.3, "domain": 0.15, "specific": 0.0}
                score = overlap + h.strength() + level_bonus.get(h.abstraction_level, 0)
                scored.append((h, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [h for h, _ in scored[:limit]]

    def validate(self, heuristic_id: str, validated: bool = True) -> None:
        """Update a heuristic based on outcome feedback."""
        h = self._heuristics.get(heuristic_id)
        if not h:
            return
        if validated:
            h.validation_count += 1
            h.confidence = min(1.0, h.confidence + 0.05)
        else:
            h.invalidation_count += 1
            h.confidence = max(0.0, h.confidence - 0.1)
        self._save_all()

    def get_stats(self) -> Dict[str, Any]:
        by_level = {}
        for h in self._heuristics.values():
            by_level[h.abstraction_level] = by_level.get(h.abstraction_level, 0) + 1
        return {
            "total": len(self._heuristics),
            "by_level": by_level,
            "avg_confidence": (
                sum(h.confidence for h in self._heuristics.values()) / len(self._heuristics)
                if self._heuristics else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Abstraction engine
    # ------------------------------------------------------------------

    def _abstract(
        self,
        task_description: str,
        task_type: str,
        what_worked: str,
        what_failed: Optional[str],
        level: str,
    ) -> str:
        """Generate an abstract heuristic from concrete experience."""
        if self._llm:
            return self._abstract_with_llm(
                task_description, task_type, what_worked, what_failed, level
            )
        return self._abstract_heuristic(
            task_description, task_type, what_worked, what_failed, level
        )

    def _abstract_with_llm(
        self, task: str, task_type: str, worked: str,
        failed: Optional[str], level: str,
    ) -> str:
        """Use LLM to generate an abstract heuristic."""
        prompt = (
            f"Given this experience, write ONE abstract {level}-level heuristic "
            f"that would help with similar {task_type} tasks in the future.\n\n"
            f"Task: {task}\n"
            f"What worked: {worked}\n"
        )
        if failed:
            prompt += f"What failed: {failed}\n"
        prompt += (
            f"\nWrite a single sentence heuristic at the '{level}' level:\n"
            f"- specific: directly references this task's domain\n"
            f"- domain: applies to all {task_type} tasks\n"
            f"- universal: applies to any problem-solving task\n"
            f"\nHeuristic:"
        )
        try:
            result = self._llm.generate(prompt)
            return result.strip()[:500]
        except Exception:
            return self._abstract_heuristic(task, task_type, worked, failed, level)

    def _abstract_heuristic(
        self, task: str, task_type: str, worked: str,
        failed: Optional[str], level: str,
    ) -> str:
        """Rule-based heuristic abstraction (no LLM needed)."""
        if level == "universal":
            # Strip domain specifics, keep the reasoning pattern
            return f"When facing {task_type} tasks: {worked[:200]}"
        elif level == "domain":
            return f"For {task_type}: {worked[:250]}"
        else:
            return f"On tasks like '{task[:80]}': {worked[:250]}"

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _find_similar(self, content: str, user_id: str) -> Optional[Heuristic]:
        """Find an existing heuristic with high word overlap."""
        content_words = set(content.lower().split())
        if len(content_words) < 3:
            return None

        for h in self._heuristics.values():
            if h.user_id != user_id:
                continue
            h_words = set(h.content.lower().split())
            if not h_words:
                continue
            overlap = len(content_words & h_words)
            jaccard = overlap / len(content_words | h_words)
            if jaccard > 0.6:
                return h
        return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _append(self, heuristic: Heuristic) -> None:
        path = os.path.join(self._dir, "heuristics.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(heuristic.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Failed to append heuristic: %s", e)

    def _save_all(self) -> None:
        path = os.path.join(self._dir, "heuristics.jsonl")
        try:
            with open(path, "w", encoding="utf-8") as f:
                for h in self._heuristics.values():
                    f.write(json.dumps(h.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Failed to save heuristics: %s", e)

    def _load(self) -> None:
        path = os.path.join(self._dir, "heuristics.jsonl")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        h = Heuristic.from_dict(data)
                        self._heuristics[h.id] = h
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError as e:
            logger.debug("Failed to load heuristics: %s", e)
