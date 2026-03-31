"""CognitionKernel — unified owner of all cognitive state primitives.

The kernel provides:
- Public access to individual stores (kernel.episodes, kernel.tasks, etc.)
- Cross-primitive coordination (checkpoint event + task update atomically)
- Cognitive state snapshot for HyperContext assembly

Zero LLM calls. Pure state management.

Usage:
    kernel = CognitionKernel(data_dir="~/.dhee/buddhi")
    kernel.tasks.create_task(user_id="u", goal="fix auth")
    state = kernel.get_cognitive_state("u", "fixing auth bug")
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CognitionKernel:
    """Owns and coordinates all cognitive state primitives.

    Stores:
        episodes   — EpisodeStore (temporal experience containers)
        tasks      — TaskStateStore (structured task tracking)
        beliefs    — BeliefStore (confidence-tracked facts)
        policies   — PolicyStore (outcome-linked condition→action rules)
        intentions — IntentionStore (prospective memory triggers)
    """

    def __init__(self, data_dir: Optional[str] = None):
        self._data_dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "buddhi"
        )
        os.makedirs(self._data_dir, exist_ok=True)

        from dhee.core.episode import EpisodeStore
        from dhee.core.task_state import TaskStateStore
        from dhee.core.belief import BeliefStore
        from dhee.core.policy import PolicyStore
        from dhee.core.intention import IntentionStore

        self.episodes = EpisodeStore(
            data_dir=os.path.join(self._data_dir, "episodes")
        )
        self.tasks = TaskStateStore(
            data_dir=os.path.join(self._data_dir, "tasks")
        )
        self.beliefs = BeliefStore(
            data_dir=os.path.join(self._data_dir, "beliefs")
        )
        self.policies = PolicyStore(
            data_dir=os.path.join(self._data_dir, "policies")
        )
        self.intentions = IntentionStore(
            data_dir=os.path.join(self._data_dir, "intentions")
        )

    # ------------------------------------------------------------------
    # Cognitive state snapshot (for HyperContext assembly)
    # ------------------------------------------------------------------

    def get_cognitive_state(
        self,
        user_id: str,
        task_description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Gather all state for HyperContext assembly.

        Returns a dict with episodes, task_states, policies, beliefs,
        triggered_intentions, and belief_warnings.
        """
        result: Dict[str, Any] = {}

        # Episodes
        try:
            recent_eps = self.episodes.retrieve_episodes(
                user_id=user_id,
                task_description=task_description,
                limit=5,
            )
            result["episodes"] = [ep.to_compact() for ep in recent_eps]
        except Exception:
            result["episodes"] = []

        # Task states
        try:
            task_states = []
            active = self.tasks.get_active_task(user_id)
            if active:
                task_states.append(active.to_compact())
            recent_tasks = self.tasks.get_recent_tasks(user_id, limit=3)
            for t in recent_tasks:
                c = t.to_compact()
                if c not in task_states:
                    task_states.append(c)
            result["task_states"] = task_states
        except Exception:
            result["task_states"] = []

        # Policies
        try:
            matched = self.policies.match_policies(
                user_id=user_id,
                task_type=task_description or "general",
                task_description=task_description or "",
                limit=3,
            )
            result["policies"] = [p.to_compact() for p in matched]
        except Exception:
            result["policies"] = []

        # Beliefs
        belief_warnings: List[str] = []
        try:
            relevant_beliefs = self.beliefs.get_relevant_beliefs(
                user_id=user_id,
                query=task_description or "",
                limit=5,
            )
            result["beliefs"] = [b.to_compact() for b in relevant_beliefs]

            # Surface contradictions as warnings
            contradictions = self.beliefs.get_contradictions(user_id)
            for b1, b2 in contradictions[:3]:
                belief_warnings.append(
                    f"Contradicting beliefs: '{b1.claim[:80]}' vs '{b2.claim[:80]}' "
                    f"(confidence: {b1.confidence:.2f} vs {b2.confidence:.2f})"
                )
        except Exception:
            result["beliefs"] = []

        result["belief_warnings"] = belief_warnings

        # Triggered intentions
        try:
            triggered = self.intentions.check_triggers(user_id, task_description)
            result["triggered_intentions"] = triggered
        except Exception:
            result["triggered_intentions"] = []

        return result

    # ------------------------------------------------------------------
    # Cross-primitive coordination
    # ------------------------------------------------------------------

    def record_checkpoint_event(
        self,
        user_id: str,
        summary: str,
        status: str = "paused",
        outcome_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record checkpoint in episode and optionally close it.

        Replaces the scattered episode logic in DheePlugin.checkpoint().
        """
        result: Dict[str, Any] = {}
        try:
            self.episodes.record_event(
                user_id=user_id,
                event_type="checkpoint",
                content=summary[:500],
                metadata={"status": status, "outcome_score": outcome_score},
            )
            if status == "completed":
                episode = self.episodes.end_episode(
                    user_id, outcome_score, summary
                )
                if episode:
                    result["episode_closed"] = episode.id
        except Exception:
            pass
        return result

    def update_task_on_checkpoint(
        self,
        user_id: str,
        goal: Optional[str] = None,
        plan: Optional[List[str]] = None,
        plan_rationale: Optional[str] = None,
        blockers: Optional[List[str]] = None,
        task_type: str = "general",
        status: str = "paused",
        outcome_score: Optional[float] = None,
        outcome_evidence: Optional[List[str]] = None,
        summary: str = "",
    ) -> Dict[str, Any]:
        """Create/update task state from checkpoint data.

        Replaces the scattered task logic in DheePlugin.checkpoint().
        """
        result: Dict[str, Any] = {}
        try:
            active_task = self.tasks.get_active_task(user_id)

            if goal or plan:
                if not active_task or active_task.goal != (goal or active_task.goal):
                    active_task = self.tasks.create_task(
                        user_id=user_id,
                        goal=goal or summary,
                        task_type=task_type,
                        plan=plan,
                        plan_rationale=plan_rationale,
                    )
                    active_task.start()
                    result["task_created"] = active_task.id
                elif plan:
                    active_task.set_plan(plan, plan_rationale)

            if active_task:
                if blockers:
                    for b in blockers:
                        active_task.add_blocker(b, severity="soft")

                if status == "completed" and outcome_score is not None:
                    if outcome_score >= 0.5:
                        active_task.complete(
                            score=outcome_score,
                            summary=summary,
                            evidence=outcome_evidence,
                        )
                    else:
                        active_task.fail(summary, evidence=outcome_evidence)
                    result["task_completed"] = active_task.id

                self.tasks.update_task(active_task)
        except Exception:
            pass
        return result

    def selective_forget(
        self,
        user_id: str,
        protected_episode_ids: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Cross-store cleanup: episodes + beliefs."""
        result: Dict[str, Any] = {}
        try:
            archived = self.episodes.selective_forget(
                user_id, protected_episode_ids
            )
            if archived > 0:
                result["episodes_archived"] = archived
        except Exception:
            pass
        try:
            self.beliefs.prune_retracted(user_id)
        except Exception:
            pass
        return result

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Persist all store state to disk."""
        for store in [
            self.episodes, self.tasks, self.beliefs,
            self.policies, self.intentions,
        ]:
            if hasattr(store, "flush"):
                try:
                    store.flush()
                except Exception:
                    pass

    def get_stats(self) -> Dict[str, Any]:
        """Aggregated stats from all stores."""
        stats: Dict[str, Any] = {}
        for name, store in [
            ("episodes", self.episodes),
            ("tasks", self.tasks),
            ("beliefs", self.beliefs),
            ("policies", self.policies),
            ("intentions", self.intentions),
        ]:
            try:
                stats[name] = store.get_stats()
            except Exception:
                stats[name] = {}
        return stats

    def __repr__(self) -> str:
        return f"CognitionKernel(data_dir={self._data_dir!r})"
