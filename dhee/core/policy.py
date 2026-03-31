"""PolicyCase — outcome-linked condition->action rules with measured utility.

A PolicyCase is NOT a text reflection like "I learned that X works better."
It is a structured, executable rule:

  condition: When task_type matches AND context contains pattern
  action:    Use approach X with parameters Y
  evidence:  Won 7/10 times when applied (outcome-tracked)
  utility:   Measured +0.23 performance delta when applied vs baseline

Policies exist at two granularities (from D2Skill, arXiv:2603.28716):
  - TASK: high-level strategy for a task type ("for bug_fix tasks, start with git blame")
  - STEP: local correction for a specific step-state ("when tests fail after a fix, check imports first")

Policies are:
  - Extracted from TaskState outcomes (what plan succeeded for what task type)
  - Validated by tracking win-rate AND measured utility across applications
  - Retrieved using similarity + utility + exploration (not just match score)
  - Pruned by utility to keep the store bounded and high-signal
  - Promoted/demoted based on performance (not just age)
  - Surfaced in HyperContext as actionable guidance

The key difference from insights: insights are descriptive ("X works"),
policies are prescriptive ("when you see A, do B, because it won C% of the time
and improved outcomes by +0.23").

Policy lifecycle: proposed -> active -> validated -> deprecated
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PolicyStatus(str, Enum):
    PROPOSED = "proposed"       # New, not yet enough data
    ACTIVE = "active"           # In use, accumulating evidence
    VALIDATED = "validated"     # Statistically significant positive
    DEPRECATED = "deprecated"   # Win rate dropped below threshold


class PolicyGranularity(str, Enum):
    TASK = "task"    # High-level strategy guidance for a task type
    STEP = "step"    # Local correction/decision support for a specific step-state


@dataclass
class PolicyCondition:
    """When this policy should fire."""
    task_types: List[str]                   # matches task_type field
    context_patterns: List[str] = field(default_factory=list)  # keywords in task description
    min_confidence: float = 0.0             # only fire if policy confidence >= this
    exclude_patterns: List[str] = field(default_factory=list)  # don't fire if these present
    step_patterns: List[str] = field(default_factory=list)     # for STEP policies: step-state keywords

    def matches(self, task_type: str, task_description: str, step_context: str = "") -> float:
        """Score how well this condition matches. Returns 0.0-1.0."""
        if not self.task_types:
            return 0.0

        # Task type match
        type_match = 1.0 if task_type in self.task_types else 0.0
        if type_match == 0.0:
            # Fuzzy: check word overlap
            type_words = set(task_type.lower().split())
            for pt in self.task_types:
                pt_words = set(pt.lower().split())
                if type_words & pt_words:
                    type_match = 0.5
                    break
        if type_match == 0.0:
            return 0.0

        desc_lower = task_description.lower()

        # Exclusion check
        for pattern in self.exclude_patterns:
            if pattern.lower() in desc_lower:
                return 0.0

        # Context pattern match
        if self.context_patterns:
            matched = sum(1 for p in self.context_patterns if p.lower() in desc_lower)
            context_score = matched / len(self.context_patterns)
        else:
            context_score = 1.0  # No pattern constraint = always matches

        # Step pattern match (for STEP-granularity policies)
        step_score = 1.0
        if self.step_patterns and step_context:
            step_lower = step_context.lower()
            step_matched = sum(1 for p in self.step_patterns if p.lower() in step_lower)
            step_score = step_matched / len(self.step_patterns)
        elif self.step_patterns and not step_context:
            step_score = 0.3  # Weak match if step context not provided

        return type_match * context_score * step_score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_types": self.task_types,
            "context_patterns": self.context_patterns,
            "min_confidence": self.min_confidence,
            "exclude_patterns": self.exclude_patterns,
            "step_patterns": self.step_patterns,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PolicyCondition:
        return cls(
            task_types=d.get("task_types", []),
            context_patterns=d.get("context_patterns", []),
            min_confidence=d.get("min_confidence", 0.0),
            exclude_patterns=d.get("exclude_patterns", []),
            step_patterns=d.get("step_patterns", []),
        )


@dataclass
class PolicyAction:
    """What to do when the condition fires."""
    approach: str               # "Use approach X"
    steps: List[str] = field(default_factory=list)  # ordered steps
    parameters: Dict[str, Any] = field(default_factory=dict)
    avoid: List[str] = field(default_factory=list)   # what NOT to do

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approach": self.approach,
            "steps": self.steps,
            "parameters": self.parameters,
            "avoid": self.avoid,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PolicyAction:
        return cls(
            approach=d.get("approach", ""),
            steps=d.get("steps", []),
            parameters=d.get("parameters", {}),
            avoid=d.get("avoid", []),
        )


@dataclass
class PolicyCase:
    """A condition->action rule with outcome tracking and measured utility."""

    id: str
    user_id: str
    name: str
    condition: PolicyCondition
    action: PolicyAction
    status: PolicyStatus

    created_at: float
    updated_at: float

    # Granularity (D2Skill dual-granularity)
    granularity: PolicyGranularity = PolicyGranularity.TASK

    # Outcome tracking
    apply_count: int = 0        # times this policy was applied
    success_count: int = 0      # times application led to success
    failure_count: int = 0      # times application led to failure

    # Utility tracking (D2Skill measured performance delta)
    utility: float = 0.0        # EMA of performance deltas
    last_delta: float = 0.0     # most recent performance delta
    cumulative_delta: float = 0.0   # sum of all deltas for lifetime tracking

    # Source tracking
    source_task_ids: List[str] = field(default_factory=list)
    source_episode_ids: List[str] = field(default_factory=list)

    tags: List[str] = field(default_factory=list)

    # Utility EMA smoothing factor
    _UTILITY_ALPHA: float = 0.3

    @property
    def win_rate(self) -> float:
        """Win rate with Laplace smoothing (add-1)."""
        return (self.success_count + 1) / (self.apply_count + 2)

    @property
    def confidence(self) -> float:
        """Confidence based on sample size (Wilson score lower bound)."""
        n = self.apply_count
        if n == 0:
            return 0.0
        p = self.success_count / n
        z = 1.96  # 95% confidence
        denominator = 1 + z * z / n
        center = p + z * z / (2 * n)
        spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
        return max(0.0, (center - spread) / denominator)

    @property
    def exploration_bonus(self) -> float:
        """UCB-style exploration bonus for under-tested policies."""
        return 1.0 / math.sqrt(self.apply_count + 1)

    @property
    def retrieval_score_components(self) -> Dict[str, float]:
        """Return components for debugging retrieval ranking."""
        return {
            "utility": self.utility,
            "confidence": self.confidence,
            "win_rate": self.win_rate,
            "exploration_bonus": self.exploration_bonus,
            "apply_count": float(self.apply_count),
        }

    def record_application(self, success: bool) -> None:
        """Record an application of this policy and its outcome (no delta)."""
        self.apply_count += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.updated_at = time.time()
        self._update_status()

    def record_outcome(
        self,
        success: bool,
        baseline_score: Optional[float] = None,
        actual_score: Optional[float] = None,
    ) -> float:
        """Record an application with optional measured performance delta.

        If baseline_score and actual_score are provided, computes the performance
        delta and updates utility via EMA. This is the core D2Skill insight:
        skills/policies are not just stored, they are re-scored based on measured
        contribution.

        Returns the computed delta (0.0 if no scores provided).
        """
        self.apply_count += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.updated_at = time.time()

        delta = 0.0
        if baseline_score is not None and actual_score is not None:
            delta = actual_score - baseline_score
            self.last_delta = delta
            self.cumulative_delta += delta
            # EMA update: utility tracks the running average performance lift
            self.utility = (
                self._UTILITY_ALPHA * delta
                + (1 - self._UTILITY_ALPHA) * self.utility
            )

        self._update_status()
        return delta

    def _update_status(self) -> None:
        """Update status based on accumulated evidence."""
        if self.apply_count < 3:
            self.status = PolicyStatus.PROPOSED
        elif self.confidence >= 0.5 and self.win_rate >= 0.6:
            self.status = PolicyStatus.VALIDATED
        elif self.apply_count >= 5 and self.win_rate < 0.4:
            self.status = PolicyStatus.DEPRECATED
        else:
            self.status = PolicyStatus.ACTIVE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "condition": self.condition.to_dict(),
            "action": self.action.to_dict(),
            "status": self.status.value,
            "granularity": self.granularity.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "apply_count": self.apply_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "utility": self.utility,
            "last_delta": self.last_delta,
            "cumulative_delta": self.cumulative_delta,
            "source_task_ids": self.source_task_ids,
            "source_episode_ids": self.source_episode_ids,
            "tags": self.tags,
        }

    def to_compact(self) -> Dict[str, Any]:
        """Compact format for HyperContext."""
        result = {
            "name": self.name,
            "level": self.granularity.value,
            "when": ", ".join(self.condition.task_types),
            "do": self.action.approach[:200],
            "win_rate": round(self.win_rate, 2),
            "utility": round(self.utility, 3),
            "confidence": round(self.confidence, 2),
            "applied": self.apply_count,
        }
        if self.action.avoid:
            result["avoid"] = self.action.avoid[:3]
        if self.action.steps:
            result["steps"] = self.action.steps[:5]
        return result

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PolicyCase:
        return cls(
            id=d["id"],
            user_id=d["user_id"],
            name=d["name"],
            condition=PolicyCondition.from_dict(d.get("condition", {})),
            action=PolicyAction.from_dict(d.get("action", {})),
            status=PolicyStatus(d.get("status", "proposed")),
            granularity=PolicyGranularity(d.get("granularity", "task")),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            apply_count=d.get("apply_count", 0),
            success_count=d.get("success_count", 0),
            failure_count=d.get("failure_count", 0),
            utility=d.get("utility", 0.0),
            last_delta=d.get("last_delta", 0.0),
            cumulative_delta=d.get("cumulative_delta", 0.0),
            source_task_ids=d.get("source_task_ids", []),
            source_episode_ids=d.get("source_episode_ids", []),
            tags=d.get("tags", []),
        )


class PolicyStore:
    """Manages policy lifecycle, matching, and learning from task outcomes.

    Retrieval uses a three-signal ranking (from D2Skill, arXiv:2603.28716):
      1. Condition match (semantic similarity to the current task context)
      2. Utility score (measured performance delta when this policy was applied)
      3. Exploration bonus (UCB-style bonus for under-tested policies)

    Policy extraction pipeline:
      1. TaskState completes with success -> analyze plan steps
      2. Find similar completed tasks -> extract common successful patterns
      3. Generate PolicyCase with condition (task_type match) and action (plan pattern)
      4. Track applications and outcomes -> promote/demote
      5. Prune low-utility policies to keep the store bounded

    This is NOT LLM-dependent. Policy extraction uses structural analysis
    of task plans and outcomes. LLM can optionally refine policy names/descriptions.
    """

    MIN_TASKS_FOR_POLICY = 3    # Need at least 3 similar completed tasks
    SIMILARITY_THRESHOLD = 0.3  # Minimum overlap for "similar" tasks
    MAX_POLICIES_PER_USER = 200  # Utility-based pruning threshold

    # Retrieval weights
    MATCH_WEIGHT = 0.4
    UTILITY_WEIGHT = 0.35
    EXPLORATION_WEIGHT = 0.25

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "policies"
        )
        os.makedirs(self._dir, exist_ok=True)
        self._policies: Dict[str, PolicyCase] = {}
        self._load()

    def create_policy(
        self,
        user_id: str,
        name: str,
        task_types: List[str],
        approach: str,
        granularity: PolicyGranularity = PolicyGranularity.TASK,
        steps: Optional[List[str]] = None,
        avoid: Optional[List[str]] = None,
        context_patterns: Optional[List[str]] = None,
        step_patterns: Optional[List[str]] = None,
        source_task_ids: Optional[List[str]] = None,
        source_episode_ids: Optional[List[str]] = None,
    ) -> PolicyCase:
        """Create a new policy from observed success patterns."""
        now = time.time()
        policy = PolicyCase(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=name,
            condition=PolicyCondition(
                task_types=task_types,
                context_patterns=context_patterns or [],
                step_patterns=step_patterns or [],
            ),
            action=PolicyAction(
                approach=approach,
                steps=steps or [],
                avoid=avoid or [],
            ),
            status=PolicyStatus.PROPOSED,
            granularity=granularity,
            created_at=now,
            updated_at=now,
            source_task_ids=source_task_ids or [],
            source_episode_ids=source_episode_ids or [],
            tags=task_types,
        )
        self._policies[policy.id] = policy
        self._save_policy(policy)
        return policy

    def create_step_policy(
        self,
        user_id: str,
        name: str,
        task_types: List[str],
        step_patterns: List[str],
        approach: str,
        avoid: Optional[List[str]] = None,
        source_task_ids: Optional[List[str]] = None,
    ) -> PolicyCase:
        """Convenience: create a STEP-granularity policy for local correction.

        Step policies fire when:
          - task_type matches AND
          - step_patterns match the current step context

        Example:
            store.create_step_policy(
                user_id="u1",
                name="check_imports_on_test_fail",
                task_types=["bug_fix"],
                step_patterns=["test", "fail", "import"],
                approach="Check for missing or circular imports before debugging logic",
                avoid=["Don't rewrite the test to make it pass"],
            )
        """
        return self.create_policy(
            user_id=user_id,
            name=name,
            task_types=task_types,
            granularity=PolicyGranularity.STEP,
            approach=approach,
            step_patterns=step_patterns,
            avoid=avoid,
            source_task_ids=source_task_ids,
        )

    def extract_from_tasks(
        self,
        user_id: str,
        completed_tasks: List[Dict[str, Any]],
        task_type: str,
    ) -> Optional[PolicyCase]:
        """Extract a policy from a cluster of completed tasks.

        Analyzes what plan patterns are common across successful completions
        of this task type, and generates a condition->action rule.
        """
        successful = [
            t for t in completed_tasks
            if t.get("outcome_score", 0) >= 0.6 and t.get("plan")
        ]

        if len(successful) < self.MIN_TASKS_FOR_POLICY:
            return None

        # Find common steps across successful plans
        step_freq: Dict[str, int] = {}
        for task in successful:
            for step in task.get("plan", []):
                if step.get("status") == "completed":
                    key = step["description"].lower().strip()
                    step_freq[key] = step_freq.get(key, 0) + 1

        # Also analyze failed tasks for "avoid" patterns
        failed = [
            t for t in completed_tasks
            if t.get("outcome_score", 0) < 0.4 and t.get("plan")
        ]
        avoid_freq: Dict[str, int] = {}
        for task in failed:
            for step in task.get("plan", []):
                if step.get("status") == "failed":
                    key = step["description"].lower().strip()
                    avoid_freq[key] = avoid_freq.get(key, 0) + 1

        # Steps that appear in >50% of successful tasks
        threshold = len(successful) * 0.5
        common_steps = [
            step for step, count in sorted(step_freq.items(), key=lambda x: -x[1])
            if count >= threshold
        ]
        avoid_steps = [
            step for step, count in sorted(avoid_freq.items(), key=lambda x: -x[1])
            if count >= max(2, len(failed) * 0.5)
        ]

        if not common_steps:
            return None

        # Check for existing similar policy
        existing = self._find_similar_policy(user_id, task_type, common_steps)
        if existing:
            # Boost existing policy instead of creating duplicate
            existing.success_count += 1
            existing.apply_count += 1
            existing.updated_at = time.time()
            self._save_policy(existing)
            return existing

        # Create new policy
        approach = f"Follow the proven plan pattern for {task_type} tasks"
        name = f"{task_type}_plan_v{len(self._policies) + 1}"

        return self.create_policy(
            user_id=user_id,
            name=name,
            task_types=[task_type],
            approach=approach,
            steps=common_steps[:10],
            avoid=avoid_steps[:5],
            source_task_ids=[t.get("id", "") for t in successful[:5]],
        )

    def extract_step_policies(
        self,
        user_id: str,
        completed_tasks: List[Dict[str, Any]],
        task_type: str,
    ) -> List[PolicyCase]:
        """Extract STEP-granularity policies from repeated failure patterns.

        Analyzes failed steps across completed tasks of the same type.
        When the same step fails >=2 times at the same position AND a
        different approach succeeded at that position in other tasks,
        creates a STEP correction policy.

        Pure structural analysis, zero LLM calls.
        """
        # Collect failed steps grouped by (position, normalized description)
        failure_counts: Dict[tuple, List[str]] = {}  # (pos, desc) -> [task_ids]
        success_at_pos: Dict[int, List[str]] = {}    # pos -> [successful step descriptions]

        for task in completed_tasks:
            plan = task.get("plan", [])
            task_id = task.get("id", "")
            task_score = task.get("outcome_score", 0)

            for idx, step in enumerate(plan):
                status = step.get("status", "pending")
                desc = step.get("description", "").lower().strip()
                if not desc:
                    continue

                if status == "failed":
                    key = (idx, desc)
                    if key not in failure_counts:
                        failure_counts[key] = []
                    failure_counts[key].append(task_id)
                elif status == "completed" and task_score >= 0.6:
                    if idx not in success_at_pos:
                        success_at_pos[idx] = []
                    success_at_pos[idx].append(desc)

        # Filter to steps that fail >=2 times
        new_policies: List[PolicyCase] = []
        for (pos, failed_desc), task_ids in failure_counts.items():
            if len(task_ids) < 2:
                continue

            # Find a successful alternative at the same position
            alternatives = success_at_pos.get(pos, [])
            if not alternatives:
                continue

            # Pick the most common successful alternative
            alt_freq: Dict[str, int] = {}
            for alt in alternatives:
                if alt != failed_desc:  # Must be different from the failing step
                    alt_freq[alt] = alt_freq.get(alt, 0) + 1
            if not alt_freq:
                continue
            best_alt = max(alt_freq, key=alt_freq.get)

            # Extract keywords from failed step as step_patterns
            stop = {"the", "a", "an", "to", "of", "in", "for", "on", "and", "or", "is", "it", "with"}
            step_patterns = [
                w for w in failed_desc.split()
                if len(w) > 2 and w not in stop
            ][:5]

            if not step_patterns:
                continue

            # Deduplicate against existing STEP policies
            existing = self._find_similar_step_policy(user_id, task_type, step_patterns)
            if existing:
                existing.success_count += 1
                existing.apply_count += 1
                existing.updated_at = time.time()
                self._save_policy(existing)
                new_policies.append(existing)
                continue

            policy = self.create_step_policy(
                user_id=user_id,
                name=f"{task_type}_step_fix_v{len(self._policies) + 1}",
                task_types=[task_type],
                step_patterns=step_patterns,
                approach=best_alt,
                avoid=[failed_desc],
                source_task_ids=task_ids[:5],
            )
            new_policies.append(policy)

        return new_policies

    def match_policies(
        self,
        user_id: str,
        task_type: str,
        task_description: str,
        step_context: str = "",
        granularity: Optional[PolicyGranularity] = None,
        limit: int = 3,
    ) -> List[PolicyCase]:
        """Find policies that match the current task context.

        Uses three-signal ranking (D2Skill):
          score = match_weight * condition_match
                + utility_weight * normalized_utility
                + exploration_weight * exploration_bonus

        Only returns non-deprecated policies. Optionally filter by granularity.
        """
        scored: List[tuple] = []
        for policy in self._policies.values():
            if policy.user_id != user_id:
                continue
            if policy.status == PolicyStatus.DEPRECATED:
                continue
            if granularity is not None and policy.granularity != granularity:
                continue

            match_score = policy.condition.matches(task_type, task_description, step_context)
            if match_score <= 0:
                continue
            if policy.confidence < policy.condition.min_confidence:
                continue

            # Normalize utility to [0, 1] range using sigmoid
            norm_utility = 1.0 / (1.0 + math.exp(-3.0 * policy.utility))

            combined = (
                self.MATCH_WEIGHT * match_score
                + self.UTILITY_WEIGHT * norm_utility
                + self.EXPLORATION_WEIGHT * policy.exploration_bonus
            )
            scored.append((policy, combined))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in scored[:limit]]

    def match_task_policies(
        self,
        user_id: str,
        task_type: str,
        task_description: str,
        limit: int = 3,
    ) -> List[PolicyCase]:
        """Convenience: match only TASK-granularity policies."""
        return self.match_policies(
            user_id=user_id,
            task_type=task_type,
            task_description=task_description,
            granularity=PolicyGranularity.TASK,
            limit=limit,
        )

    def match_step_policies(
        self,
        user_id: str,
        task_type: str,
        task_description: str,
        step_context: str,
        limit: int = 3,
    ) -> List[PolicyCase]:
        """Convenience: match only STEP-granularity policies for local correction."""
        return self.match_policies(
            user_id=user_id,
            task_type=task_type,
            task_description=task_description,
            step_context=step_context,
            granularity=PolicyGranularity.STEP,
            limit=limit,
        )

    def record_outcome(
        self,
        policy_id: str,
        success: bool,
        task_id: Optional[str] = None,
        baseline_score: Optional[float] = None,
        actual_score: Optional[float] = None,
    ) -> Optional[float]:
        """Record the outcome of applying a policy.

        If baseline_score and actual_score are provided, computes the performance
        delta and updates the policy's utility score. Returns the delta.
        """
        policy = self._policies.get(policy_id)
        if not policy:
            return None
        delta = policy.record_outcome(
            success=success,
            baseline_score=baseline_score,
            actual_score=actual_score,
        )
        if task_id and task_id not in policy.source_task_ids:
            policy.source_task_ids.append(task_id)
        self._save_policy(policy)
        return delta

    def prune(self, user_id: str, max_policies: Optional[int] = None) -> Dict[str, Any]:
        """Prune low-utility policies to keep the store bounded.

        Removes deprecated policies first, then lowest-utility policies
        until count is within budget. Policies with status VALIDATED are
        protected from pruning.

        Returns stats about what was pruned.
        """
        budget = max_policies or self.MAX_POLICIES_PER_USER
        user_policies = [
            p for p in self._policies.values()
            if p.user_id == user_id
        ]

        if len(user_policies) <= budget:
            return {"pruned": 0, "total": len(user_policies)}

        # Sort by pruning priority: deprecated first, then by utility ascending
        def prune_priority(p: PolicyCase) -> tuple:
            # Protected: validated policies sort last
            protected = 0 if p.status == PolicyStatus.VALIDATED else 1
            # Deprecated sort first (highest prune priority)
            deprecated = 1 if p.status == PolicyStatus.DEPRECATED else 0
            return (protected, deprecated, -p.utility, -p.apply_count)

        candidates = sorted(user_policies, key=prune_priority, reverse=True)

        pruned = 0
        while len(user_policies) - pruned > budget and candidates:
            victim = candidates.pop(0)
            if victim.status == PolicyStatus.VALIDATED:
                break  # Don't prune validated policies
            self._delete_policy(victim.id)
            pruned += 1

        return {
            "pruned": pruned,
            "total": len(user_policies) - pruned,
            "budget": budget,
        }

    def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        policies = list(self._policies.values())
        if user_id:
            policies = [p for p in policies if p.user_id == user_id]

        by_status = {}
        by_granularity = {}
        for p in policies:
            by_status[p.status.value] = by_status.get(p.status.value, 0) + 1
            by_granularity[p.granularity.value] = by_granularity.get(p.granularity.value, 0) + 1

        validated = [p for p in policies if p.status == PolicyStatus.VALIDATED]
        with_utility = [p for p in policies if p.apply_count > 0]
        return {
            "total": len(policies),
            "by_status": by_status,
            "by_granularity": by_granularity,
            "validated_count": len(validated),
            "avg_win_rate": (
                sum(p.win_rate for p in validated) / len(validated)
                if validated else 0.0
            ),
            "avg_utility": (
                sum(p.utility for p in with_utility) / len(with_utility)
                if with_utility else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_similar_policy(
        self, user_id: str, task_type: str, steps: List[str],
    ) -> Optional[PolicyCase]:
        """Find an existing policy with similar steps for the same task type."""
        step_words = set()
        for s in steps:
            step_words.update(s.lower().split())

        for policy in self._policies.values():
            if policy.user_id != user_id:
                continue
            if task_type not in policy.condition.task_types:
                continue

            policy_words = set()
            for s in policy.action.steps:
                policy_words.update(s.lower().split())

            if not policy_words:
                continue
            overlap = len(step_words & policy_words) / len(step_words | policy_words)
            if overlap > self.SIMILARITY_THRESHOLD:
                return policy

        return None

    def _find_similar_step_policy(
        self, user_id: str, task_type: str, step_patterns: List[str],
    ) -> Optional[PolicyCase]:
        """Find an existing STEP policy with similar step_patterns."""
        pattern_words = set(w.lower() for w in step_patterns)
        if not pattern_words:
            return None

        for policy in self._policies.values():
            if policy.user_id != user_id:
                continue
            if policy.granularity != PolicyGranularity.STEP:
                continue
            if task_type not in policy.condition.task_types:
                continue

            existing_words = set(w.lower() for w in policy.condition.step_patterns)
            if not existing_words:
                continue
            overlap = len(pattern_words & existing_words) / len(pattern_words | existing_words)
            if overlap > self.SIMILARITY_THRESHOLD:
                return policy

        return None

    def _delete_policy(self, policy_id: str) -> None:
        self._policies.pop(policy_id, None)
        path = os.path.join(self._dir, f"{policy_id}.json")
        try:
            os.remove(path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_policy(self, policy: PolicyCase) -> None:
        path = os.path.join(self._dir, f"{policy.id}.json")
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(policy.to_dict(), f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as e:
            logger.debug("Failed to save policy %s: %s", policy.id, e)

    def _load(self) -> None:
        if not os.path.isdir(self._dir):
            return
        for fname in os.listdir(self._dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                policy = PolicyCase.from_dict(data)
                self._policies[policy.id] = policy
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.debug("Failed to load policy %s: %s", fname, e)

    def flush(self) -> None:
        for policy in self._policies.values():
            self._save_policy(policy)
