"""Contrastive Memory — learn from success/failure pairs.

Based on ReasoningBank (arXiv:2509.25140): Memory-Aware Test-Time Scaling.
The key insight: storing BOTH what worked AND what failed for similar tasks
produces dramatically better future decisions than storing successes alone.

Every time checkpoint() receives both what_worked and what_failed,
a ContrastivePair is created. These pairs are:
  1. Surfaced in HyperContext as "contrasts" — the agent sees what to do AND avoid
  2. Used for DPO training data in BuddhiMini's progressive trainer
  3. Used to re-rank retrieval results (contrastive boost)

The MaTTS scoring algorithm re-ranks retrieval candidates by checking
whether they align with success approaches or failure approaches for
similar past tasks.
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
class ContrastivePair:
    """A success/failure pair from a completed task."""

    id: str
    task_description: str
    task_type: str
    success_approach: str
    failure_approach: str
    outcome_delta: float            # how much better success was (0-1)
    created_at: float
    user_id: str = "default"
    tags: List[str] = field(default_factory=list)
    validation_count: int = 0       # times this contrast proved useful

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_description": self.task_description,
            "task_type": self.task_type,
            "success_approach": self.success_approach,
            "failure_approach": self.failure_approach,
            "outcome_delta": self.outcome_delta,
            "created_at": self.created_at,
            "user_id": self.user_id,
            "tags": self.tags,
            "validation_count": self.validation_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ContrastivePair:
        return cls(
            id=d["id"],
            task_description=d["task_description"],
            task_type=d.get("task_type", "general"),
            success_approach=d["success_approach"],
            failure_approach=d["failure_approach"],
            outcome_delta=d.get("outcome_delta", 0.5),
            created_at=d.get("created_at", time.time()),
            user_id=d.get("user_id", "default"),
            tags=d.get("tags", []),
            validation_count=d.get("validation_count", 0),
        )

    def to_compact(self) -> Dict[str, str]:
        """Compact format for HyperContext — what the agent sees."""
        return {
            "task": self.task_description[:200],
            "do": self.success_approach[:300],
            "avoid": self.failure_approach[:300],
            "confidence": round(min(1.0, 0.5 + 0.1 * self.validation_count), 2),
        }


class ContrastiveStore:
    """Stores and retrieves contrastive pairs.

    Persistence: JSONL file on disk. Retrieval: keyword matching
    (no embedder dependency — works on edge devices).
    """

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "contrastive"
        )
        os.makedirs(self._dir, exist_ok=True)
        self._pairs: Dict[str, ContrastivePair] = {}
        self._load()

    def add_pair(
        self,
        task_description: str,
        success_approach: str,
        failure_approach: str,
        task_type: str = "general",
        outcome_delta: float = 0.5,
        user_id: str = "default",
        tags: Optional[List[str]] = None,
    ) -> ContrastivePair:
        """Add a contrastive pair from a completed task."""
        pair = ContrastivePair(
            id=str(uuid.uuid4()),
            task_description=task_description,
            task_type=task_type,
            success_approach=success_approach,
            failure_approach=failure_approach,
            outcome_delta=outcome_delta,
            created_at=time.time(),
            user_id=user_id,
            tags=tags or [task_type],
        )
        self._pairs[pair.id] = pair
        self._append(pair)
        return pair

    def retrieve_contrasts(
        self,
        task_description: str,
        user_id: str = "default",
        limit: int = 3,
    ) -> List[ContrastivePair]:
        """Find contrastive pairs relevant to a task description.

        Uses word-overlap scoring — fast, no embedder required.
        """
        query_words = set(task_description.lower().split())
        if not query_words:
            return []

        scored: List[tuple] = []
        for pair in self._pairs.values():
            if pair.user_id != user_id:
                continue
            pair_words = set(pair.task_description.lower().split())
            pair_words |= set(pair.task_type.lower().split())
            pair_words |= set(t.lower() for t in pair.tags)
            overlap = len(query_words & pair_words)
            if overlap > 0:
                # Boost by validation count and outcome_delta
                score = overlap + pair.validation_count * 0.5 + pair.outcome_delta
                scored.append((pair, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in scored[:limit]]

    def matts_score(
        self,
        query: str,
        candidate_texts: List[str],
        user_id: str = "default",
    ) -> List[float]:
        """Memory-Aware Test-Time Scaling (MaTTS) — re-rank by contrastive evidence.

        For each candidate, compute how much it aligns with success approaches
        vs failure approaches from relevant contrastive pairs.

        Returns a list of boost factors (0.0 to 1.0) parallel to candidate_texts.
        """
        if not candidate_texts:
            return []

        # Find relevant contrasts
        contrasts = self.retrieve_contrasts(query, user_id=user_id, limit=5)
        if not contrasts:
            return [0.0] * len(candidate_texts)

        boosts = []
        for text in candidate_texts:
            text_lower = text.lower()
            text_words = set(text_lower.split())
            success_signal = 0.0
            failure_signal = 0.0

            for pair in contrasts:
                success_words = set(pair.success_approach.lower().split())
                failure_words = set(pair.failure_approach.lower().split())

                success_overlap = len(text_words & success_words)
                failure_overlap = len(text_words & failure_words)

                success_signal += success_overlap * pair.outcome_delta
                failure_signal += failure_overlap * pair.outcome_delta

            # Normalize to 0-1 range. Positive = aligns with success.
            total = success_signal + failure_signal
            if total > 0:
                boost = (success_signal - failure_signal) / total
                boosts.append(max(0.0, min(1.0, (boost + 1) / 2)))
            else:
                boosts.append(0.0)

        return boosts

    def validate(self, pair_id: str) -> None:
        """Mark a contrastive pair as validated (proved useful)."""
        pair = self._pairs.get(pair_id)
        if pair:
            pair.validation_count += 1
            self._save_all()

    def get_dpo_pairs(self, limit: int = 50) -> List[Dict[str, str]]:
        """Export contrastive pairs as DPO training data."""
        pairs = sorted(
            self._pairs.values(),
            key=lambda p: p.validation_count + p.outcome_delta,
            reverse=True,
        )
        return [
            {
                "prompt": f"[TASK] {p.task_description}\n[TYPE] {p.task_type}",
                "chosen": p.success_approach,
                "rejected": p.failure_approach,
            }
            for p in pairs[:limit]
        ]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_pairs": len(self._pairs),
            "validated_pairs": sum(
                1 for p in self._pairs.values() if p.validation_count > 0
            ),
            "task_types": list({p.task_type for p in self._pairs.values()}),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _append(self, pair: ContrastivePair) -> None:
        path = os.path.join(self._dir, "pairs.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(pair.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Failed to append contrastive pair: %s", e)

    def _save_all(self) -> None:
        path = os.path.join(self._dir, "pairs.jsonl")
        try:
            with open(path, "w", encoding="utf-8") as f:
                for pair in self._pairs.values():
                    f.write(json.dumps(pair.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Failed to save contrastive pairs: %s", e)

    def _load(self) -> None:
        path = os.path.join(self._dir, "pairs.jsonl")
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
                        pair = ContrastivePair.from_dict(data)
                        self._pairs[pair.id] = pair
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError as e:
            logger.debug("Failed to load contrastive pairs: %s", e)
