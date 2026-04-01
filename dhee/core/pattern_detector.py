"""FailurePatternDetector — temporal failure pattern detection via decision stumps.

Discovers WHEN and WHERE tasks fail by mining TaskState + Episode metadata.
Zero LLM calls. Pure statistics.

Algorithm: For each feature, find the single binary split that maximizes
information gain for predicting success vs failure. This is a "decision stump"
— the simplest non-trivial classifier.

Output: TemporalPattern objects like:
    "Tasks fail 2.3x more often when duration > 30 min (68% vs 30% baseline, n=42)"

These patterns are converted to PolicyCase objects with tags=["temporal_pattern"]
and surfaced via the existing HyperContext pipeline.

Honest about limits:
    - Decision stumps only find single-feature, axis-aligned splits
    - Can't detect interaction effects ("fails when duration > 30 AND preceded by refactor")
    - With 10-50 samples, overfitting risk is real — conservative thresholds mitigate
    - Temporal features like day_of_week need many weeks of data to be meaningful
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from dhee.core.task_state import TaskState, TaskStatus, StepStatus
from dhee.core.episode import Episode


# ---------------------------------------------------------------------------
# Feature Vector
# ---------------------------------------------------------------------------


@dataclass
class TaskFeatureVector:
    """Feature vector extracted from a completed TaskState + optional Episode."""

    task_id: str
    success: bool  # outcome_score >= 0.6

    # Task-derived features (all Optional — None when data unavailable)
    duration_minutes: Optional[float] = None
    step_count: Optional[float] = None
    failed_step_ratio: Optional[float] = None
    blocker_count: Optional[float] = None
    hard_blocker_count: Optional[float] = None
    outcome_score: Optional[float] = None
    task_type_hash: Optional[float] = None
    time_of_day_bucket: Optional[float] = None
    day_of_week: Optional[float] = None
    plan_completion_ratio: Optional[float] = None
    preceding_task_score: Optional[float] = None
    preceding_task_failed: Optional[float] = None

    # Episode-derived features
    episode_event_count: Optional[float] = None
    episode_duration_minutes: Optional[float] = None
    memory_count: Optional[float] = None
    recall_count: Optional[float] = None
    connection_count: Optional[float] = None

    # All numeric feature names for enumeration
    FEATURE_NAMES: List[str] = field(default=None, repr=False)

    def __post_init__(self):
        # Not serialized — used for iteration only
        object.__setattr__(self, "FEATURE_NAMES", [
            "duration_minutes", "step_count", "failed_step_ratio",
            "blocker_count", "hard_blocker_count", "outcome_score",
            "task_type_hash", "time_of_day_bucket", "day_of_week",
            "plan_completion_ratio", "preceding_task_score",
            "preceding_task_failed", "episode_event_count",
            "episode_duration_minutes", "memory_count", "recall_count",
            "connection_count",
        ])

    def get_feature(self, name: str) -> Optional[float]:
        """Get a feature value by name."""
        return getattr(self, name, None)


# Human-readable feature descriptions for pattern output
_FEATURE_LABELS = {
    "duration_minutes": "task duration (minutes)",
    "step_count": "number of plan steps",
    "failed_step_ratio": "ratio of failed steps",
    "blocker_count": "number of blockers",
    "hard_blocker_count": "number of hard blockers",
    "outcome_score": "outcome score",
    "task_type_hash": "task type category",
    "time_of_day_bucket": "time of day",
    "day_of_week": "day of week",
    "plan_completion_ratio": "plan completion ratio",
    "preceding_task_score": "previous task's score",
    "preceding_task_failed": "previous task failed",
    "episode_event_count": "episode event count",
    "episode_duration_minutes": "episode duration (minutes)",
    "memory_count": "memories used",
    "recall_count": "memory recalls",
    "connection_count": "cross-primitive connections",
}


# ---------------------------------------------------------------------------
# Feature Extraction
# ---------------------------------------------------------------------------


def extract_features(
    tasks: List[TaskState],
    episodes: Optional[Dict[str, Episode]] = None,
) -> List[TaskFeatureVector]:
    """Extract feature vectors from terminal tasks.

    Args:
        tasks: List of TaskState objects, sorted by updated_at ascending.
        episodes: Optional dict of episode_id -> Episode for enrichment.

    Returns:
        List of TaskFeatureVector for tasks with terminal status
        (COMPLETED, FAILED, ABANDONED).
    """
    episodes = episodes or {}
    terminal_statuses = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.ABANDONED}

    # Filter and sort by updated_at for preceding-task computation
    terminal = [t for t in tasks if t.status in terminal_statuses]
    terminal.sort(key=lambda t: t.updated_at)

    vectors: List[TaskFeatureVector] = []
    prev_by_user: Dict[str, TaskState] = {}  # user_id -> previous terminal task

    for task in terminal:
        score = task.outcome_score if task.outcome_score is not None else 0.0
        success = score >= 0.6

        fv = TaskFeatureVector(task_id=task.id, success=success)

        # --- Task-derived features ---

        # Duration
        if task.completed_at and task.created_at:
            fv.duration_minutes = (task.completed_at - task.created_at) / 60.0

        # Plan analysis
        if task.plan:
            fv.step_count = float(len(task.plan))
            failed_steps = sum(
                1 for s in task.plan if s.status == StepStatus.FAILED
            )
            completed_steps = sum(
                1 for s in task.plan
                if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
            )
            fv.failed_step_ratio = failed_steps / len(task.plan)
            fv.plan_completion_ratio = completed_steps / len(task.plan)

        # Blockers
        fv.blocker_count = float(len(task.blockers))
        fv.hard_blocker_count = float(
            sum(1 for b in task.blockers if b.severity == "hard")
        )

        # Outcome
        fv.outcome_score = score

        # Task type hash (bucketed to reduce cardinality)
        fv.task_type_hash = float(hash(task.task_type) % 64)

        # Temporal features from created_at
        if task.created_at:
            import datetime
            dt = datetime.datetime.fromtimestamp(task.created_at)
            fv.time_of_day_bucket = float(dt.hour // 6)  # 0-3
            fv.day_of_week = float(dt.weekday())  # 0=Mon, 6=Sun

        # Preceding task features
        prev = prev_by_user.get(task.user_id)
        if prev is not None:
            prev_score = prev.outcome_score if prev.outcome_score is not None else 0.0
            fv.preceding_task_score = prev_score
            fv.preceding_task_failed = 1.0 if prev.status == TaskStatus.FAILED else 0.0

        # --- Episode-derived features ---
        episode = episodes.get(task.episode_id) if task.episode_id else None
        if episode is not None:
            fv.episode_event_count = float(len(episode.events))
            fv.episode_duration_minutes = episode.duration_seconds / 60.0
            fv.memory_count = float(len(episode.memory_ids))
            fv.recall_count = float(
                sum(1 for e in episode.events if e.event_type == "memory_recall")
            )
            fv.connection_count = float(episode.connection_count)

        vectors.append(fv)
        prev_by_user[task.user_id] = task

    return vectors


# ---------------------------------------------------------------------------
# TemporalPattern
# ---------------------------------------------------------------------------


@dataclass
class TemporalPattern:
    """A discovered failure-predictive pattern from decision stump analysis."""

    id: str
    feature: str                    # e.g., "duration_minutes"
    threshold: float                # e.g., 30.0
    direction: str                  # "above" | "below" — failure concentrates here
    confidence: float               # Information gain (0-1 normalized)
    lift: float                     # P(fail|condition) / P(fail)
    sample_size: int                # Total data points used
    failure_rate_condition: float   # P(fail|condition)
    failure_rate_baseline: float    # P(fail) overall
    description: str                # Human-readable summary
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "feature": self.feature,
            "threshold": self.threshold,
            "direction": self.direction,
            "confidence": round(self.confidence, 4),
            "lift": round(self.lift, 3),
            "sample_size": self.sample_size,
            "failure_rate_condition": round(self.failure_rate_condition, 4),
            "failure_rate_baseline": round(self.failure_rate_baseline, 4),
            "description": self.description,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TemporalPattern:
        return cls(
            id=d["id"],
            feature=d["feature"],
            threshold=d["threshold"],
            direction=d["direction"],
            confidence=d.get("confidence", 0.0),
            lift=d.get("lift", 1.0),
            sample_size=d.get("sample_size", 0),
            failure_rate_condition=d.get("failure_rate_condition", 0.0),
            failure_rate_baseline=d.get("failure_rate_baseline", 0.0),
            description=d.get("description", ""),
            created_at=d.get("created_at", time.time()),
        )

    def to_compact(self) -> Dict[str, Any]:
        """Compact format for HyperContext surfacing."""
        return {
            "pattern": self.description,
            "confidence": round(self.confidence, 2),
            "lift": round(self.lift, 1),
            "samples": self.sample_size,
        }


# ---------------------------------------------------------------------------
# Decision Stump Algorithm
# ---------------------------------------------------------------------------


def _entropy(positives: int, negatives: int) -> float:
    """Binary entropy of a label distribution.

    H(p) = -(p * log2(p) + (1-p) * log2(1-p))
    """
    total = positives + negatives
    if total == 0:
        return 0.0
    p = positives / total
    if p == 0.0 or p == 1.0:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def _information_gain(
    parent_fail: int,
    parent_success: int,
    left_fail: int,
    left_success: int,
    right_fail: int,
    right_success: int,
) -> float:
    """Information gain of a binary split.

    IG = H(parent) - [|left|/|parent| * H(left) + |right|/|parent| * H(right)]
    """
    parent_entropy = _entropy(parent_fail, parent_success)
    total = parent_fail + parent_success
    left_total = left_fail + left_success
    right_total = right_fail + right_success
    if total == 0 or left_total == 0 or right_total == 0:
        return 0.0
    child_entropy = (
        (left_total / total) * _entropy(left_fail, left_success)
        + (right_total / total) * _entropy(right_fail, right_success)
    )
    return parent_entropy - child_entropy


def _find_best_split(
    values: List[Tuple[float, bool]],
    min_split_size: int = 3,
) -> Optional[Tuple[float, float, str, float, float]]:
    """Find the threshold that maximizes information gain for one feature.

    Args:
        values: List of (feature_value, is_failure) pairs.
        min_split_size: Minimum samples each side of the split must have.

    Returns:
        (threshold, gain, direction, fail_rate_left, fail_rate_right)
        or None if no valid split found.

    Algorithm:
        1. Sort by feature value
        2. Walk through sorted values, trying each midpoint as threshold
        3. Left = values <= threshold, Right = values > threshold
        4. Compute information gain at each candidate split
        5. Return the split with maximum gain
    """
    if len(values) < 2 * min_split_size:
        return None

    # Sort by feature value
    sorted_vals = sorted(values, key=lambda x: x[0])

    total_fail = sum(1 for _, f in sorted_vals if f)
    total_success = len(sorted_vals) - total_fail

    if total_fail == 0 or total_success == 0:
        return None  # No split possible if all same label

    left_fail = 0
    left_success = 0
    right_fail = total_fail
    right_success = total_success

    best_gain = 0.0
    best_threshold = None
    best_direction = "above"
    best_fail_rate_left = 0.0
    best_fail_rate_right = 0.0

    for i in range(len(sorted_vals) - 1):
        # Move current sample from right to left
        if sorted_vals[i][1]:  # is_failure
            left_fail += 1
            right_fail -= 1
        else:
            left_success += 1
            right_success -= 1

        # Skip if same value as next (can't split between equal values)
        if sorted_vals[i][0] == sorted_vals[i + 1][0]:
            continue

        # Enforce minimum split size
        left_total = left_fail + left_success
        right_total = right_fail + right_success
        if left_total < min_split_size or right_total < min_split_size:
            continue

        gain = _information_gain(
            total_fail, total_success,
            left_fail, left_success,
            right_fail, right_success,
        )

        if gain > best_gain:
            best_gain = gain
            best_threshold = (sorted_vals[i][0] + sorted_vals[i + 1][0]) / 2.0

            left_fail_rate = left_fail / left_total if left_total > 0 else 0.0
            right_fail_rate = right_fail / right_total if right_total > 0 else 0.0

            best_fail_rate_left = left_fail_rate
            best_fail_rate_right = right_fail_rate
            best_direction = "above" if right_fail_rate > left_fail_rate else "below"

    if best_threshold is None or best_gain <= 0:
        return None

    return (
        best_threshold,
        best_gain,
        best_direction,
        best_fail_rate_left,
        best_fail_rate_right,
    )


# ---------------------------------------------------------------------------
# FailurePatternDetector
# ---------------------------------------------------------------------------


class FailurePatternDetector:
    """Detects temporal/contextual failure patterns via decision stumps.

    Zero LLM calls. Pure statistics. Requires ≥10 completed tasks.

    Usage:
        detector = FailurePatternDetector()
        features = extract_features(tasks, episodes)
        patterns = detector.detect_and_describe(features)
    """

    MIN_SAMPLES: int = 10           # Won't run with fewer tasks
    MIN_SPLIT_SIZE: int = 3         # Each side of split needs ≥3 samples
    MIN_INFORMATION_GAIN: float = 0.02  # Ignore trivial patterns
    MIN_LIFT: float = 1.3           # Must increase failure rate by 30%+
    MAX_PATTERNS: int = 10          # Return top N patterns by information gain

    def detect_patterns(
        self,
        features: List[TaskFeatureVector],
    ) -> List[TemporalPattern]:
        """Run decision stump analysis on each feature.

        For each feature in TaskFeatureVector:
            1. Extract (value, is_failure) pairs, dropping None values
            2. If fewer than MIN_SAMPLES pairs, skip
            3. Find best binary split maximizing information gain
            4. If gain > MIN_INFORMATION_GAIN and lift > MIN_LIFT, emit pattern
            5. Return top MAX_PATTERNS by information gain

        Returns empty list if total samples < MIN_SAMPLES.
        """
        if len(features) < self.MIN_SAMPLES:
            return []

        # Get feature names from a sample vector
        if not features:
            return []
        feature_names = features[0].FEATURE_NAMES

        # Baseline failure rate
        total_failures = sum(1 for fv in features if not fv.success)
        baseline_failure_rate = total_failures / len(features)

        if baseline_failure_rate == 0.0 or baseline_failure_rate == 1.0:
            return []  # Nothing to predict if all same outcome

        patterns: List[TemporalPattern] = []

        for feat_name in feature_names:
            # Collect (value, is_failure) pairs, skipping None
            pairs: List[Tuple[float, bool]] = []
            for fv in features:
                val = fv.get_feature(feat_name)
                if val is not None:
                    pairs.append((val, not fv.success))

            if len(pairs) < self.MIN_SAMPLES:
                continue

            result = _find_best_split(pairs, self.MIN_SPLIT_SIZE)
            if result is None:
                continue

            threshold, gain, direction, fail_rate_left, fail_rate_right = result

            if gain < self.MIN_INFORMATION_GAIN:
                continue

            # Compute lift
            if direction == "above":
                condition_fail_rate = fail_rate_right
            else:
                condition_fail_rate = fail_rate_left

            if baseline_failure_rate > 0:
                lift = condition_fail_rate / baseline_failure_rate
            else:
                lift = 0.0

            if lift < self.MIN_LIFT:
                continue

            pattern = TemporalPattern(
                id=str(uuid.uuid4()),
                feature=feat_name,
                threshold=round(threshold, 4),
                direction=direction,
                confidence=round(gain, 4),
                lift=round(lift, 3),
                sample_size=len(pairs),
                failure_rate_condition=round(condition_fail_rate, 4),
                failure_rate_baseline=round(baseline_failure_rate, 4),
                description="",  # Filled by detect_and_describe
            )
            patterns.append(pattern)

        # Sort by information gain descending, take top N
        patterns.sort(key=lambda p: p.confidence, reverse=True)
        return patterns[:self.MAX_PATTERNS]

    def detect_and_describe(
        self,
        features: List[TaskFeatureVector],
    ) -> List[TemporalPattern]:
        """detect_patterns() + generate human-readable descriptions.

        Description template:
            "Tasks fail {lift:.1f}x more often when {feature_label}
             {direction} {threshold} ({failure_rate_condition:.0%}
             vs {failure_rate_baseline:.0%} baseline, n={sample_size})"
        """
        patterns = self.detect_patterns(features)

        for pattern in patterns:
            label = _FEATURE_LABELS.get(pattern.feature, pattern.feature)
            cond_pct = f"{pattern.failure_rate_condition:.0%}"
            base_pct = f"{pattern.failure_rate_baseline:.0%}"

            # Format threshold cleanly
            if pattern.threshold == int(pattern.threshold):
                thresh_str = str(int(pattern.threshold))
            else:
                thresh_str = f"{pattern.threshold:.1f}"

            pattern.description = (
                f"Tasks fail {pattern.lift:.1f}x more often when "
                f"{label} {pattern.direction} {thresh_str} "
                f"({cond_pct} vs {base_pct} baseline, n={pattern.sample_size})"
            )

        return patterns
