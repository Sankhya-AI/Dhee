"""PolicyCase — outcome-linked condition->action rules.

A PolicyCase is NOT a text reflection like "I learned that X works better."
It is a structured, executable rule:

  condition: When task_type matches AND context contains pattern
  action:    Use approach X with parameters Y
  evidence:  Won 7/10 times when applied (outcome-tracked)

Policies are:
  - Extracted from TaskState outcomes (what plan succeeded for what task type)
  - Validated by tracking win-rate across applications
  - Promoted/demoted based on performance (not just age)
  - Surfaced in HyperContext as actionable guidance

The key difference from insights: insights are descriptive ("X works"),
policies are prescriptive ("when you see A, do B, because it won C% of the time").

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


@dataclass
class PolicyCondition:
    """When this policy should fire."""
    task_types: List[str]                   # matches task_type field
    context_patterns: List[str] = field(default_factory=list)  # keywords in task description
    min_confidence: float = 0.0             # only fire if policy confidence >= this
    exclude_patterns: List[str] = field(default_factory=list)  # don't fire if these present

    def matches(self, task_type: str, task_description: str) -> float:
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

        return type_match * context_score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_types": self.task_types,
            "context_patterns": self.context_patterns,
            "min_confidence": self.min_confidence,
            "exclude_patterns": self.exclude_patterns,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PolicyCondition:
        return cls(
            task_types=d.get("task_types", []),
            context_patterns=d.get("context_patterns", []),
            min_confidence=d.get("min_confidence", 0.0),
            exclude_patterns=d.get("exclude_patterns", []),
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
    """A condition->action rule with outcome tracking."""

    id: str
    user_id: str
    name: str
    condition: PolicyCondition
    action: PolicyAction
    status: PolicyStatus

    created_at: float
    updated_at: float

    # Outcome tracking
    apply_count: int = 0        # times this policy was applied
    success_count: int = 0      # times application led to success
    failure_count: int = 0      # times application led to failure

    # Source tracking
    source_task_ids: List[str] = field(default_factory=list)
    source_episode_ids: List[str] = field(default_factory=list)

    tags: List[str] = field(default_factory=list)

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

    def record_application(self, success: bool) -> None:
        """Record an application of this policy and its outcome."""
        self.apply_count += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.updated_at = time.time()

        # Auto-promote/demote based on evidence
        self._update_status()

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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "apply_count": self.apply_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "source_task_ids": self.source_task_ids,
            "source_episode_ids": self.source_episode_ids,
            "tags": self.tags,
        }

    def to_compact(self) -> Dict[str, Any]:
        """Compact format for HyperContext."""
        result = {
            "name": self.name,
            "when": ", ".join(self.condition.task_types),
            "do": self.action.approach[:200],
            "win_rate": round(self.win_rate, 2),
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
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            apply_count=d.get("apply_count", 0),
            success_count=d.get("success_count", 0),
            failure_count=d.get("failure_count", 0),
            source_task_ids=d.get("source_task_ids", []),
            source_episode_ids=d.get("source_episode_ids", []),
            tags=d.get("tags", []),
        )


class PolicyStore:
    """Manages policy lifecycle, matching, and learning from task outcomes.

    Policy extraction pipeline:
      1. TaskState completes with success → analyze plan steps
      2. Find similar completed tasks → extract common successful patterns
      3. Generate PolicyCase with condition (task_type match) and action (plan pattern)
      4. Track applications and outcomes → promote/demote

    This is NOT LLM-dependent. Policy extraction uses structural analysis
    of task plans and outcomes. LLM can optionally refine policy names/descriptions.
    """

    MIN_TASKS_FOR_POLICY = 3    # Need at least 3 similar completed tasks
    SIMILARITY_THRESHOLD = 0.3  # Minimum overlap for "similar" tasks

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
        steps: Optional[List[str]] = None,
        avoid: Optional[List[str]] = None,
        context_patterns: Optional[List[str]] = None,
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
            ),
            action=PolicyAction(
                approach=approach,
                steps=steps or [],
                avoid=avoid or [],
            ),
            status=PolicyStatus.PROPOSED,
            created_at=now,
            updated_at=now,
            source_task_ids=source_task_ids or [],
            source_episode_ids=source_episode_ids or [],
            tags=task_types,
        )
        self._policies[policy.id] = policy
        self._save_policy(policy)
        return policy

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
        avoid_freq: Dict[str, int] = {}
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

    def match_policies(
        self,
        user_id: str,
        task_type: str,
        task_description: str,
        limit: int = 3,
    ) -> List[PolicyCase]:
        """Find policies that match the current task context.

        Returns policies sorted by (match_score * confidence).
        Only returns non-deprecated policies.
        """
        scored: List[tuple] = []
        for policy in self._policies.values():
            if policy.user_id != user_id:
                continue
            if policy.status == PolicyStatus.DEPRECATED:
                continue

            match_score = policy.condition.matches(task_type, task_description)
            if match_score > 0 and policy.confidence >= policy.condition.min_confidence:
                combined = match_score * (0.5 + 0.5 * policy.confidence)
                scored.append((policy, combined))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in scored[:limit]]

    def record_outcome(
        self,
        policy_id: str,
        success: bool,
        task_id: Optional[str] = None,
    ) -> None:
        """Record the outcome of applying a policy."""
        policy = self._policies.get(policy_id)
        if not policy:
            return
        policy.record_application(success)
        if task_id and task_id not in policy.source_task_ids:
            policy.source_task_ids.append(task_id)
        self._save_policy(policy)

    def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        policies = list(self._policies.values())
        if user_id:
            policies = [p for p in policies if p.user_id == user_id]

        by_status = {}
        for p in policies:
            by_status[p.status.value] = by_status.get(p.status.value, 0) + 1

        validated = [p for p in policies if p.status == PolicyStatus.VALIDATED]
        return {
            "total": len(policies),
            "by_status": by_status,
            "validated_count": len(validated),
            "avg_win_rate": (
                sum(p.win_rate for p in validated) / len(validated)
                if validated else 0.0
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
