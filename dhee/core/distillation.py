"""Replay-driven semantic distillation (CLS consolidation).

During sleep cycles, the ReplayDistiller samples recent episodic memories,
groups them by scene or time window, and uses an LLM to extract durable
semantic facts. This models the hippocampus-to-neocortex transfer in
Complementary Learning Systems theory.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from dhee.memory.utils import strip_code_fences
from dhee.utils.prompts import DISTILLATION_PROMPT

if TYPE_CHECKING:
    from dhee.configs.base import DistillationConfig
    from dhee.db.sqlite import SQLiteManager
    from dhee.llms.base import BaseLLM

logger = logging.getLogger(__name__)


class ReplayDistiller:
    """Extracts semantic knowledge from episodic memory batches."""

    def __init__(
        self,
        db: "SQLiteManager",
        llm: "BaseLLM",
        config: "DistillationConfig",
    ):
        self.db = db
        self.llm = llm
        self.config = config

    def run(
        self,
        user_id: str,
        date_str: Optional[str] = None,
        memory_add_fn: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run one distillation cycle for a user.

        Args:
            user_id: The user whose episodic memories to distill.
            date_str: Target date (defaults to yesterday).
            memory_add_fn: Callable to add a memory (typically Memory.add).
                           Required for actual distillation; if None, dry-run only.

        Returns:
            Stats dict with episodes_sampled, semantic_created, etc.
        """
        if not self.config.enable_distillation:
            return {"skipped": True, "reason": "distillation disabled"}

        target_date = date_str or (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).date().isoformat()

        window_hours = self.config.distillation_time_window_hours
        created_after = f"{target_date}T00:00:00"
        created_before = f"{target_date}T23:59:59.999999"

        # Sample recent episodic memories
        episodes = self.db.get_episodic_memories(
            user_id,
            created_after=created_after,
            created_before=created_before,
            limit=self.config.distillation_batch_size * 5,
        )

        if len(episodes) < self.config.distillation_min_episodes:
            return {
                "skipped": True,
                "reason": "insufficient episodes",
                "episodes_found": len(episodes),
                "min_required": self.config.distillation_min_episodes,
            }

        # Group into batches
        batches = self._group_episodes(episodes)

        total_created = 0
        total_dedup = 0
        total_errors = 0

        for batch in batches:
            try:
                created, dedup = self._distill_batch(
                    user_id=user_id,
                    batch=batch,
                    memory_add_fn=memory_add_fn,
                )
                total_created += created
                total_dedup += dedup
            except Exception as e:
                logger.warning("Distillation batch failed: %s", e)
                total_errors += 1

        # Log the run
        run_id = self.db.log_distillation_run(
            user_id=user_id,
            episodes_sampled=len(episodes),
            semantic_created=total_created,
            semantic_deduplicated=total_dedup,
            errors=total_errors,
        )

        return {
            "run_id": run_id,
            "episodes_sampled": len(episodes),
            "batches_processed": len(batches),
            "semantic_created": total_created,
            "semantic_deduplicated": total_dedup,
            "errors": total_errors,
        }

    def _group_episodes(
        self, episodes: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """Group episodes by scene_id or into time-window chunks."""
        if self.config.distillation_scene_grouping:
            # Group by scene_id first
            scene_groups: Dict[Optional[str], List[Dict[str, Any]]] = {}
            for ep in episodes:
                scene_id = ep.get("scene_id")
                scene_groups.setdefault(scene_id, []).append(ep)

            batches = []
            for scene_id, group in scene_groups.items():
                # Split large scene groups into sub-batches
                batch_size = self.config.distillation_batch_size
                for i in range(0, len(group), batch_size):
                    batches.append(group[i : i + batch_size])
            return batches

        # Fallback: chunk by batch_size
        batch_size = self.config.distillation_batch_size
        return [
            episodes[i : i + batch_size]
            for i in range(0, len(episodes), batch_size)
        ]

    def _distill_batch(
        self,
        user_id: str,
        batch: List[Dict[str, Any]],
        memory_add_fn: Optional[Any],
    ) -> tuple:
        """Distill a single batch of episodes. Returns (created, deduplicated)."""
        # Build the episodes text for the prompt
        episode_texts = []
        episode_ids = []
        for ep in batch:
            ep_id = ep.get("id", "unknown")
            episode_ids.append(ep_id)
            content = ep.get("memory", "")
            created_at = ep.get("created_at", "")
            episode_texts.append(f"[{ep_id}] ({created_at}): {content}")

        episodes_str = "\n".join(episode_texts)
        prompt = DISTILLATION_PROMPT.format(
            episodes=episodes_str,
            max_facts=self.config.max_semantic_per_batch,
        )

        raw_response = self.llm.generate(prompt)
        cleaned = strip_code_fences(raw_response)

        try:
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Distillation LLM returned invalid JSON: %.200s", raw_response)
            return (0, 0)

        facts = parsed.get("semantic_facts", [])
        if not isinstance(facts, list):
            return (0, 0)

        created = 0
        deduplicated = 0

        for fact in facts[: self.config.max_semantic_per_batch]:
            content = fact.get("content", "").strip()
            if not content:
                continue

            importance = fact.get("importance", "medium")
            source_eps = fact.get("source_episodes", episode_ids)

            if memory_add_fn is not None:
                result = memory_add_fn(
                    content,
                    user_id=user_id,
                    infer=False,
                    initial_layer="lml",
                    initial_strength=0.8,
                    metadata={
                        "is_distilled": True,
                        "distillation_source_count": len(source_eps),
                        "importance": importance,
                        "memory_type": "semantic",
                    },
                )

                # Check if it was deduplicated (NOOP/SUBSUMED)
                results = result.get("results", [])
                if results:
                    first = results[0]
                    event = first.get("event", "ADD")
                    if event in ("NOOP", "SUBSUMED"):
                        deduplicated += 1
                    else:
                        created += 1
                        # Record provenance
                        semantic_id = first.get("id")
                        if semantic_id:
                            try:
                                self.db.add_distillation_provenance(
                                    semantic_memory_id=semantic_id,
                                    episodic_memory_ids=source_eps,
                                    run_id=str(uuid.uuid4()),
                                )
                            except Exception as e:
                                logger.warning("Failed to record provenance: %s", e)

        return (created, deduplicated)
