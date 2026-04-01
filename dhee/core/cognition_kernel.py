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

    @staticmethod
    def _state_error(component: str, exc: Exception) -> Dict[str, str]:
        return {
            "component": component,
            "error": f"{type(exc).__name__}: {exc}",
        }

    @staticmethod
    def _operation_error(
        operation: str,
        component: str,
        exc: Exception,
        *,
        target: Optional[str] = None,
    ) -> Dict[str, str]:
        entry = {
            "operation": operation,
            "component": component,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if target is not None:
            entry["target"] = target
        return entry

    def _record_operation_error(
        self,
        result: Dict[str, Any],
        *,
        operation: str,
        component: str,
        exc: Exception,
        target: Optional[str] = None,
    ) -> None:
        if target is None:
            logger.exception(
                "CognitionKernel %s failed for %s", operation, component
            )
        else:
            logger.exception(
                "CognitionKernel %s failed for %s (%s)",
                operation,
                component,
                target,
            )
        result.setdefault("errors", []).append(
            self._operation_error(
                operation,
                component,
                exc,
                target=target,
            )
        )

    @staticmethod
    def _merge_operation_errors(
        result: Dict[str, Any], nested_result: Optional[Dict[str, Any]]
    ) -> None:
        if not nested_result:
            return
        nested_errors = nested_result.get("errors", [])
        if nested_errors:
            result.setdefault("errors", []).extend(nested_errors)

    def get_cognitive_state(
        self,
        user_id: str,
        task_description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Gather all state for HyperContext assembly.

        Returns a dict with episodes, task_states, policies, beliefs,
        triggered_intentions, and belief_warnings.
        """
        result: Dict[str, Any] = {"state_errors": []}
        state_errors: List[Dict[str, str]] = result["state_errors"]

        # Episodes
        try:
            recent_eps = self.episodes.retrieve_episodes(
                user_id=user_id,
                task_description=task_description,
                limit=5,
            )
            result["episodes"] = [ep.to_compact() for ep in recent_eps]
        except Exception as exc:
            logger.exception("Failed to load cognitive state component 'episodes'")
            state_errors.append(self._state_error("episodes", exc))
            result["episodes"] = []

        # Task states + active step context
        step_context = ""
        active_task_type = task_description or "general"
        try:
            task_states = []
            active = self.tasks.get_active_task(user_id)
            if active:
                task_states.append(active.to_compact())
                current = active.current_step
                if current:
                    step_context = current.description
                active_task_type = active.task_type or active_task_type
            recent_tasks = self.tasks.get_recent_tasks(user_id, limit=3)
            for t in recent_tasks:
                c = t.to_compact()
                if c not in task_states:
                    task_states.append(c)
            result["task_states"] = task_states
        except Exception as exc:
            logger.exception("Failed to load cognitive state component 'task_states'")
            state_errors.append(self._state_error("task_states", exc))
            result["task_states"] = []

        result["active_step"] = step_context if step_context else None

        # Policies (pass step_context for better matching)
        try:
            matched = self.policies.match_policies(
                user_id=user_id,
                task_type=active_task_type,
                task_description=task_description or "",
                step_context=step_context,
                limit=3,
            )
            result["policies"] = [p.to_compact() for p in matched]
        except Exception as exc:
            logger.exception("Failed to load cognitive state component 'policies'")
            state_errors.append(self._state_error("policies", exc))
            result["policies"] = []

        # Step policies (separate, for operational context)
        try:
            if step_context:
                step_matched = self.policies.match_step_policies(
                    user_id=user_id,
                    task_type=active_task_type,
                    task_description=task_description or "",
                    step_context=step_context,
                    limit=3,
                )
                result["step_policies"] = [p.to_compact() for p in step_matched]
            else:
                result["step_policies"] = []
        except Exception as exc:
            logger.exception("Failed to load cognitive state component 'step_policies'")
            state_errors.append(self._state_error("step_policies", exc))
            result["step_policies"] = []

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
        except Exception as exc:
            logger.exception("Failed to load cognitive state component 'beliefs'")
            state_errors.append(self._state_error("beliefs", exc))
            result["beliefs"] = []

        result["belief_warnings"] = belief_warnings

        # Triggered intentions
        try:
            triggered = self.intentions.check_triggers(user_id, task_description)
            result["triggered_intentions"] = triggered
        except Exception as exc:
            logger.exception("Failed to load cognitive state component 'triggered_intentions'")
            state_errors.append(self._state_error("triggered_intentions", exc))
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
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_checkpoint_event",
                component="episodes.record_event",
                exc=exc,
            )

        # Wire episode.connection_count for cross-primitive links
        connections = 0
        try:
            active_task = self.tasks.get_active_task(user_id)
            if active_task:
                connections += 1
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_checkpoint_event",
                component="tasks.get_active_task",
                exc=exc,
            )

        try:
            matched_policies = self.policies.match_policies(
                user_id, summary[:50], summary[:200], limit=3,
            )
            connections += len(matched_policies)
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_checkpoint_event",
                component="policies.match_policies",
                exc=exc,
            )

        if connections > 0:
            try:
                self.episodes.increment_connections(user_id, connections)
            except Exception as exc:
                self._record_operation_error(
                    result,
                    operation="record_checkpoint_event",
                    component="episodes.increment_connections",
                    exc=exc,
                )

        if status == "completed":
            try:
                episode = self.episodes.end_episode(
                    user_id, outcome_score, summary
                )
                if episode:
                    result["episode_closed"] = episode.id
            except Exception as exc:
                self._record_operation_error(
                    result,
                    operation="record_checkpoint_event",
                    component="episodes.end_episode",
                    exc=exc,
                )
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
        active_task = None
        try:
            active_task = self.tasks.get_active_task(user_id)
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="update_task_on_checkpoint",
                component="tasks.get_active_task",
                exc=exc,
            )

        if goal or plan:
            if not active_task or active_task.goal != (goal or active_task.goal):
                try:
                    active_task = self.tasks.create_task(
                        user_id=user_id,
                        goal=goal or summary,
                        task_type=task_type,
                        plan=plan,
                        plan_rationale=plan_rationale,
                    )
                    active_task.start()
                    result["task_created"] = active_task.id
                except Exception as exc:
                    self._record_operation_error(
                        result,
                        operation="update_task_on_checkpoint",
                        component="tasks.create_task",
                        exc=exc,
                    )
            elif plan:
                try:
                    active_task.set_plan(plan, plan_rationale)
                except Exception as exc:
                    self._record_operation_error(
                        result,
                        operation="update_task_on_checkpoint",
                        component="tasks.set_plan",
                        exc=exc,
                    )

        if active_task:
            if blockers:
                for blocker in blockers:
                    try:
                        active_task.add_blocker(blocker, severity="soft")
                    except Exception as exc:
                        self._record_operation_error(
                            result,
                            operation="update_task_on_checkpoint",
                            component="tasks.add_blocker",
                            exc=exc,
                            target=blocker,
                        )

            if status == "completed" and outcome_score is not None:
                try:
                    if outcome_score >= 0.5:
                        active_task.complete(
                            score=outcome_score,
                            summary=summary,
                            evidence=outcome_evidence,
                        )
                    else:
                        active_task.fail(summary, evidence=outcome_evidence)
                    result["task_completed"] = active_task.id
                except Exception as exc:
                    self._record_operation_error(
                        result,
                        operation="update_task_on_checkpoint",
                        component="tasks.complete_or_fail",
                        exc=exc,
                    )

            try:
                self.tasks.update_task(active_task)
            except Exception as exc:
                self._record_operation_error(
                    result,
                    operation="update_task_on_checkpoint",
                    component="tasks.update_task",
                    exc=exc,
                )

            # Record outcomes on STEP policies for completed/failed steps
            if status == "completed" and active_task.plan:
                step_updates = 0
                for step in active_task.plan:
                    if step.status.value == "completed":
                        step_result = self.record_step_outcome(
                            user_id,
                            task_type,
                            step.description,
                            success=True,
                            actual_score=outcome_score,
                        )
                    elif step.status.value == "failed":
                        step_result = self.record_step_outcome(
                            user_id,
                            task_type,
                            step.description,
                            success=False,
                            actual_score=outcome_score,
                        )
                    else:
                        continue

                    step_updates += step_result.get("policies_updated", 0)
                    self._merge_operation_errors(result, step_result)

                if step_updates:
                    result["step_policies_updated"] = step_updates
        return result

    def record_step_outcome(
        self,
        user_id: str,
        task_type: str,
        step_description: str,
        success: bool,
        baseline_score: Optional[float] = None,
        actual_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record outcome on STEP policies matching a completed/failed step.

        Finds matching STEP policies and records their outcomes.
        Zero LLM calls.
        """
        result: Dict[str, Any] = {"policies_updated": 0}
        try:
            matched = self.policies.match_step_policies(
                user_id=user_id,
                task_type=task_type,
                task_description=f"{task_type} task",
                step_context=step_description,
                limit=5,
            )
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_step_outcome",
                component="policies.match_step_policies",
                exc=exc,
            )
            return result

        for policy in matched:
            try:
                self.policies.record_outcome(
                    policy.id,
                    success=success,
                    baseline_score=baseline_score,
                    actual_score=actual_score,
                )
                result["policies_updated"] += 1
            except Exception as exc:
                self._record_operation_error(
                    result,
                    operation="record_step_outcome",
                    component="policies.record_outcome",
                    exc=exc,
                    target=policy.id,
                )
        return result

    def record_learning_outcomes(
        self,
        user_id: str,
        task_type: str,
        success: bool,
        baseline_score: Optional[float] = None,
        actual_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Cross-structure learning from task outcomes.

        Handles all cross-primitive feedback that was previously scattered
        in Buddhi.reflect(). Owns:
        - Policy outcome recording (TASK-level)
        - Step policy extraction from completed tasks
        - Belief-policy interaction (challenged beliefs degrade policies)
        - Intention outcome recording
        - Episode connection wiring
        - Temporal failure pattern detection (decision stumps)

        Zero LLM calls. Pure structural feedback.
        """
        result: Dict[str, Any] = {
            "policies_updated": 0,
            "step_policies_created": 0,
            "intentions_updated": 0,
            "beliefs_policy_decays": 0,
            "patterns_detected": 0,
        }
        task_desc = f"{task_type} task"

        # 1. Record outcomes on matched TASK policies
        try:
            matched = self.policies.match_policies(
                user_id, task_type, task_desc,
            )
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_learning_outcomes",
                component="policies.match_policies",
                exc=exc,
            )
        else:
            for policy in matched:
                try:
                    self.policies.record_outcome(
                        policy.id,
                        success=success,
                        baseline_score=baseline_score,
                        actual_score=actual_score,
                    )
                    result["policies_updated"] += 1
                except Exception as exc:
                    self._record_operation_error(
                        result,
                        operation="record_learning_outcomes",
                        component="policies.record_outcome",
                        exc=exc,
                        target=policy.id,
                    )

        # 2. Extract TASK + STEP policies from completed tasks
        try:
            completed = self.tasks.get_tasks_by_type(
                user_id, task_type, limit=10,
            )
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_learning_outcomes",
                component="tasks.get_tasks_by_type",
                exc=exc,
            )
        else:
            if len(completed) >= 3:
                task_dicts = [t.to_dict() for t in completed]
                try:
                    self.policies.extract_from_tasks(
                        user_id, task_dicts, task_type,
                    )
                except Exception as exc:
                    self._record_operation_error(
                        result,
                        operation="record_learning_outcomes",
                        component="policies.extract_from_tasks",
                        exc=exc,
                    )
                try:
                    step_policies = self.policies.extract_step_policies(
                        user_id, task_dicts, task_type,
                    )
                    result["step_policies_created"] = len(step_policies)
                except Exception as exc:
                    self._record_operation_error(
                        result,
                        operation="record_learning_outcomes",
                        component="policies.extract_step_policies",
                        exc=exc,
                    )

        # 3. Belief-policy interaction: challenged beliefs degrade dependent policies
        if not success:
            try:
                relevant_beliefs = self.beliefs.get_relevant_beliefs(
                    user_id, task_desc, limit=3,
                )
            except Exception as exc:
                self._record_operation_error(
                    result,
                    operation="record_learning_outcomes",
                    component="beliefs.get_relevant_beliefs",
                    exc=exc,
                )
            else:
                try:
                    user_policies = list(self.policies.get_user_policies(user_id))
                except Exception as exc:
                    self._record_operation_error(
                        result,
                        operation="record_learning_outcomes",
                        component="policies.get_user_policies",
                        exc=exc,
                    )
                    user_policies = []

                for belief in relevant_beliefs:
                    if belief.confidence < 0.3:
                        claim_words = set(belief.claim.lower().split()[:5])
                        for policy in user_policies:
                            approach_words = set(policy.action.approach.lower().split())
                            if len(claim_words & approach_words) >= 2:
                                try:
                                    self.policies.decay_utility(policy.id, factor=0.8)
                                    result["beliefs_policy_decays"] += 1
                                except Exception as exc:
                                    self._record_operation_error(
                                        result,
                                        operation="record_learning_outcomes",
                                        component="policies.decay_utility",
                                        exc=exc,
                                        target=policy.id,
                                    )

        # 4. Intention outcome recording
        try:
            triggered = self.intentions.get_triggered_pending_feedback(user_id)
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_learning_outcomes",
                component="intentions.get_triggered_pending_feedback",
                exc=exc,
            )
        else:
            for intention in triggered:
                try:
                    self.intentions.record_outcome(
                        intention.id,
                        useful=success,
                        outcome_score=actual_score,
                    )
                    result["intentions_updated"] += 1
                except Exception as exc:
                    self._record_operation_error(
                        result,
                        operation="record_learning_outcomes",
                        component="intentions.record_outcome",
                        exc=exc,
                        target=intention.id,
                    )

        # 5. Episode connection wiring
        connections = 0
        try:
            active_task = self.tasks.get_active_task(user_id)
            if active_task:
                connections += 1
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_learning_outcomes",
                component="tasks.get_active_task",
                exc=exc,
            )
        try:
            matched_policies = self.policies.match_policies(
                user_id, task_type, task_desc, limit=3,
            )
            connections += len(matched_policies)
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_learning_outcomes",
                component="policies.match_policies",
                exc=exc,
            )
        if connections > 0:
            try:
                self.episodes.increment_connections(user_id, connections)
            except Exception as exc:
                self._record_operation_error(
                    result,
                    operation="record_learning_outcomes",
                    component="episodes.increment_connections",
                    exc=exc,
                )

        # 6. Temporal failure pattern detection (decision stumps)
        try:
            from dhee.core.pattern_detector import (
                FailurePatternDetector, extract_features,
            )
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="record_learning_outcomes",
                component="pattern_detector.import",
                exc=exc,
            )
        else:
            try:
                recent = self.tasks.get_recent_tasks(
                    user_id, limit=100, include_terminal=True,
                )
            except Exception as exc:
                self._record_operation_error(
                    result,
                    operation="record_learning_outcomes",
                    component="tasks.get_recent_tasks",
                    exc=exc,
                )
            else:
                terminal = [t for t in recent if t.is_terminal]
                if len(terminal) >= FailurePatternDetector.MIN_SAMPLES:
                    episode_map = {}
                    for task in terminal:
                        if not task.episode_id:
                            continue
                        try:
                            episode = self.episodes.get_episode(task.episode_id)
                            if episode:
                                episode_map[episode.id] = episode
                        except Exception as exc:
                            self._record_operation_error(
                                result,
                                operation="record_learning_outcomes",
                                component="episodes.get_episode",
                                exc=exc,
                                target=task.episode_id,
                            )

                    try:
                        features = extract_features(terminal, episode_map)
                        detector = FailurePatternDetector()
                        patterns = detector.detect_and_describe(features)
                    except Exception as exc:
                        self._record_operation_error(
                            result,
                            operation="record_learning_outcomes",
                            component="pattern_detector.detect_and_describe",
                            exc=exc,
                        )
                    else:
                        for pattern in patterns[:3]:
                            try:
                                stored = self._store_pattern_as_policy(
                                    user_id, task_type, pattern,
                                )
                                if stored:
                                    result["patterns_detected"] += 1
                            except Exception as exc:
                                self._record_operation_error(
                                    result,
                                    operation="record_learning_outcomes",
                                    component="policies.store_temporal_pattern",
                                    exc=exc,
                                )

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
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="selective_forget",
                component="episodes.selective_forget",
                exc=exc,
            )
        try:
            self.beliefs.prune_retracted(user_id)
        except Exception as exc:
            self._record_operation_error(
                result,
                operation="selective_forget",
                component="beliefs.prune_retracted",
                exc=exc,
            )
        return result

    # ------------------------------------------------------------------
    # Pattern detection helpers
    # ------------------------------------------------------------------

    def _store_pattern_as_policy(
        self,
        user_id: str,
        task_type: str,
        pattern: Any,
    ) -> Optional[Any]:
        """Convert a detected TemporalPattern into an enriched PolicyCase.

        Deduplication: checks if a policy with tags=['temporal_pattern']
        and matching feature+direction+threshold already exists.

        Returns the created/existing PolicyCase, or None.
        """
        # Dedup check: look for existing temporal_pattern policy with same signature
        pattern_sig = f"{pattern.feature}_{pattern.direction}_{pattern.threshold}"
        for existing in self.policies.get_user_policies(user_id):
            if "temporal_pattern" in existing.tags:
                existing_sig = "_".join(
                    p for p in existing.condition.context_patterns[:3]
                )
                if existing_sig == pattern_sig:
                    return existing  # Already stored

        # Build avoidance description
        avoid_desc = (
            f"Proceeding when {pattern.feature} is "
            f"{pattern.direction} {pattern.threshold}"
        )

        policy = self.policies.create_policy(
            user_id=user_id,
            name=f"temporal_{pattern.feature}_{pattern.direction}",
            task_types=[task_type],
            approach=pattern.description,
            context_patterns=[
                pattern.feature, pattern.direction, str(pattern.threshold),
            ],
            avoid=[avoid_desc],
        )
        policy.tags = ["auto_detected", "temporal_pattern"]
        self.policies._save_policy(policy)
        return policy

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Persist all store state to disk."""
        errors: List[Dict[str, str]] = []
        for store in [
            ("episodes", self.episodes),
            ("tasks", self.tasks),
            ("beliefs", self.beliefs),
            ("policies", self.policies),
            ("intentions", self.intentions),
        ]:
            name, store_instance = store
            if hasattr(store_instance, "flush"):
                try:
                    store_instance.flush()
                except Exception as exc:
                    logger.exception(
                        "CognitionKernel flush failed for %s", name
                    )
                    errors.append(
                        self._operation_error(
                            "flush",
                            f"{name}.flush",
                            exc,
                        )
                    )
        if errors:
            detail = "; ".join(
                f"{entry['component']}: {entry['error']}" for entry in errors
            )
            raise RuntimeError(f"Failed to flush cognition stores: {detail}")

    def get_stats(self) -> Dict[str, Any]:
        """Aggregated stats from all stores."""
        stats: Dict[str, Any] = {}
        errors: List[Dict[str, str]] = []
        for name, store in [
            ("episodes", self.episodes),
            ("tasks", self.tasks),
            ("beliefs", self.beliefs),
            ("policies", self.policies),
            ("intentions", self.intentions),
        ]:
            try:
                stats[name] = store.get_stats()
            except Exception as exc:
                logger.exception("CognitionKernel get_stats failed for %s", name)
                errors.append(
                    self._operation_error(
                        "get_stats",
                        f"{name}.get_stats",
                        exc,
                    )
                )
                stats[name] = {"error": f"{type(exc).__name__}: {exc}"}
        if errors:
            stats["errors"] = errors
        return stats

    def __repr__(self) -> str:
        return f"CognitionKernel(data_dir={self._data_dir!r})"
