"""TaskState — structured task tracking as a first-class cognitive object.

A TaskState is NOT a memory or a checkpoint summary. It is a live,
structured representation of what the agent is trying to do:

  goal:      What the agent is trying to achieve
  plan:      Ordered list of steps the agent intends to take
  progress:  Which steps are done, in-progress, or blocked
  blockers:  What's preventing progress (with severity)
  outcome:   Final result (success/partial/failure + evidence)
  context:   Links to episodes, beliefs, policies that inform this task

TaskState enables:
  - Resumption: agent picks up exactly where it left off
  - Reflection: structured comparison of plan vs actual
  - Policy learning: which plans succeed for which task types
  - Cross-session continuity: task survives agent restart

Lifecycle: created -> in_progress -> blocked? -> completed | failed | abandoned
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class TaskStep:
    """A single step in a task plan."""
    id: str
    description: str
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    outcome_note: Optional[str] = None

    def start(self) -> None:
        self.status = StepStatus.IN_PROGRESS
        self.started_at = time.time()

    def complete(self, note: Optional[str] = None) -> None:
        self.status = StepStatus.COMPLETED
        self.completed_at = time.time()
        if note:
            self.outcome_note = note

    def fail(self, note: Optional[str] = None) -> None:
        self.status = StepStatus.FAILED
        self.completed_at = time.time()
        if note:
            self.outcome_note = note

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "outcome_note": self.outcome_note,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TaskStep:
        return cls(
            id=d["id"],
            description=d["description"],
            status=StepStatus(d.get("status", "pending")),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            outcome_note=d.get("outcome_note"),
        )


@dataclass
class Blocker:
    """Something preventing task progress."""
    id: str
    description: str
    severity: str           # "hard" (can't proceed) | "soft" (can work around)
    created_at: float
    resolved_at: Optional[float] = None
    resolution: Optional[str] = None

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    def resolve(self, resolution: str) -> None:
        self.resolved_at = time.time()
        self.resolution = resolution

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "severity": self.severity,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Blocker:
        return cls(
            id=d["id"],
            description=d["description"],
            severity=d.get("severity", "soft"),
            created_at=d.get("created_at", time.time()),
            resolved_at=d.get("resolved_at"),
            resolution=d.get("resolution"),
        )


@dataclass
class TaskState:
    """Structured representation of an agent's current task."""

    id: str
    user_id: str
    goal: str
    task_type: str
    status: TaskStatus

    created_at: float
    updated_at: float
    completed_at: Optional[float] = None

    # Plan
    plan: List[TaskStep] = field(default_factory=list)
    plan_rationale: Optional[str] = None

    # Blockers
    blockers: List[Blocker] = field(default_factory=list)

    # Outcome
    outcome_score: Optional[float] = None
    outcome_summary: Optional[str] = None
    outcome_evidence: List[str] = field(default_factory=list)

    # Cross-references
    episode_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    subtask_ids: List[str] = field(default_factory=list)
    related_belief_ids: List[str] = field(default_factory=list)
    related_policy_ids: List[str] = field(default_factory=list)

    # Context
    context: Dict[str, Any] = field(default_factory=dict)

    def add_step(self, description: str) -> TaskStep:
        """Add a step to the plan."""
        step = TaskStep(id=str(uuid.uuid4()), description=description)
        self.plan.append(step)
        self.updated_at = time.time()
        return step

    def set_plan(self, steps: List[str], rationale: Optional[str] = None) -> List[TaskStep]:
        """Set the full plan (replaces existing steps)."""
        self.plan = [
            TaskStep(id=str(uuid.uuid4()), description=desc)
            for desc in steps
        ]
        self.plan_rationale = rationale
        self.updated_at = time.time()
        return self.plan

    def start(self) -> None:
        """Mark task as in-progress."""
        self.status = TaskStatus.IN_PROGRESS
        self.updated_at = time.time()
        # Auto-start first pending step
        for step in self.plan:
            if step.status == StepStatus.PENDING:
                step.start()
                break

    def add_blocker(self, description: str, severity: str = "soft") -> Blocker:
        """Add a blocker to the task."""
        blocker = Blocker(
            id=str(uuid.uuid4()),
            description=description,
            severity=severity,
            created_at=time.time(),
        )
        self.blockers.append(blocker)
        if severity == "hard":
            self.status = TaskStatus.BLOCKED
        self.updated_at = time.time()
        return blocker

    def resolve_blocker(self, blocker_id: str, resolution: str) -> None:
        """Resolve a blocker."""
        for blocker in self.blockers:
            if blocker.id == blocker_id:
                blocker.resolve(resolution)
                break

        # If all hard blockers resolved, resume
        has_hard = any(
            b.severity == "hard" and not b.is_resolved
            for b in self.blockers
        )
        if not has_hard and self.status == TaskStatus.BLOCKED:
            self.status = TaskStatus.IN_PROGRESS
        self.updated_at = time.time()

    def advance_step(self, note: Optional[str] = None) -> Optional[TaskStep]:
        """Complete the current step and start the next one.

        Returns the newly started step, or None if all done.
        """
        current = self.current_step
        if current:
            current.complete(note)

        # Find next pending step
        for step in self.plan:
            if step.status == StepStatus.PENDING:
                step.start()
                self.updated_at = time.time()
                return step

        self.updated_at = time.time()
        return None

    def complete(
        self,
        score: float,
        summary: str,
        evidence: Optional[List[str]] = None,
    ) -> None:
        """Mark task as completed with outcome."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = time.time()
        self.outcome_score = score
        self.outcome_summary = summary
        self.outcome_evidence = evidence or []
        self.updated_at = time.time()

        # Auto-complete remaining in-progress steps
        for step in self.plan:
            if step.status == StepStatus.IN_PROGRESS:
                step.complete()

    def fail(self, summary: str, evidence: Optional[List[str]] = None) -> None:
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.completed_at = time.time()
        self.outcome_score = 0.0
        self.outcome_summary = summary
        self.outcome_evidence = evidence or []
        self.updated_at = time.time()

    @property
    def current_step(self) -> Optional[TaskStep]:
        """Get the currently in-progress step."""
        for step in self.plan:
            if step.status == StepStatus.IN_PROGRESS:
                return step
        return None

    @property
    def progress_fraction(self) -> float:
        """Fraction of plan completed (0.0 to 1.0)."""
        if not self.plan:
            return 0.0
        done = sum(1 for s in self.plan if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED))
        return done / len(self.plan)

    @property
    def active_blockers(self) -> List[Blocker]:
        return [b for b in self.blockers if not b.is_resolved]

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.ABANDONED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "goal": self.goal,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "plan": [s.to_dict() for s in self.plan],
            "plan_rationale": self.plan_rationale,
            "blockers": [b.to_dict() for b in self.blockers],
            "outcome_score": self.outcome_score,
            "outcome_summary": self.outcome_summary,
            "outcome_evidence": self.outcome_evidence,
            "episode_id": self.episode_id,
            "parent_task_id": self.parent_task_id,
            "subtask_ids": self.subtask_ids,
            "related_belief_ids": self.related_belief_ids,
            "related_policy_ids": self.related_policy_ids,
            "context": self.context,
        }

    def to_compact(self) -> Dict[str, Any]:
        """Compact format for HyperContext."""
        result = {
            "id": self.id,
            "goal": self.goal[:200],
            "task_type": self.task_type,
            "status": self.status.value,
            "progress": round(self.progress_fraction, 2),
        }
        current = self.current_step
        if current:
            result["current_step"] = current.description[:200]
        if self.active_blockers:
            result["blockers"] = [b.description[:100] for b in self.active_blockers[:3]]
        if self.outcome_summary:
            result["outcome"] = self.outcome_summary[:200]
        return result

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TaskState:
        return cls(
            id=d["id"],
            user_id=d["user_id"],
            goal=d["goal"],
            task_type=d.get("task_type", "general"),
            status=TaskStatus(d.get("status", "created")),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            completed_at=d.get("completed_at"),
            plan=[TaskStep.from_dict(s) for s in d.get("plan", [])],
            plan_rationale=d.get("plan_rationale"),
            blockers=[Blocker.from_dict(b) for b in d.get("blockers", [])],
            outcome_score=d.get("outcome_score"),
            outcome_summary=d.get("outcome_summary"),
            outcome_evidence=d.get("outcome_evidence", []),
            episode_id=d.get("episode_id"),
            parent_task_id=d.get("parent_task_id"),
            subtask_ids=d.get("subtask_ids", []),
            related_belief_ids=d.get("related_belief_ids", []),
            related_policy_ids=d.get("related_policy_ids", []),
            context=d.get("context", {}),
        )


class TaskStateStore:
    """Manages TaskState lifecycle and cross-session persistence."""

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "tasks"
        )
        os.makedirs(self._dir, exist_ok=True)
        self._tasks: Dict[str, TaskState] = {}
        self._active_tasks: Dict[str, str] = {}  # user_id -> task_id (most recent active)
        self._load()

    def create_task(
        self,
        user_id: str,
        goal: str,
        task_type: str = "general",
        plan: Optional[List[str]] = None,
        plan_rationale: Optional[str] = None,
        episode_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
    ) -> TaskState:
        """Create a new task with optional initial plan."""
        now = time.time()
        task = TaskState(
            id=str(uuid.uuid4()),
            user_id=user_id,
            goal=goal,
            task_type=task_type,
            status=TaskStatus.CREATED,
            created_at=now,
            updated_at=now,
            episode_id=episode_id,
            parent_task_id=parent_task_id,
        )
        if plan:
            task.set_plan(plan, plan_rationale)

        # Link to parent
        if parent_task_id and parent_task_id in self._tasks:
            self._tasks[parent_task_id].subtask_ids.append(task.id)
            self._save_task(self._tasks[parent_task_id])

        self._tasks[task.id] = task
        self._active_tasks[user_id] = task.id
        self._save_task(task)
        return task

    def get_task(self, task_id: str) -> Optional[TaskState]:
        return self._tasks.get(task_id)

    def get_active_task(self, user_id: str) -> Optional[TaskState]:
        """Get the most recent non-terminal task for a user."""
        task_id = self._active_tasks.get(user_id)
        if task_id:
            task = self._tasks.get(task_id)
            if task and not task.is_terminal:
                return task

        # Search for most recent non-terminal
        candidates = [
            t for t in self._tasks.values()
            if t.user_id == user_id and not t.is_terminal
        ]
        if candidates:
            candidates.sort(key=lambda t: t.updated_at, reverse=True)
            self._active_tasks[user_id] = candidates[0].id
            return candidates[0]
        return None

    def get_recent_tasks(
        self,
        user_id: str,
        limit: int = 5,
        include_terminal: bool = True,
    ) -> List[TaskState]:
        """Get recent tasks for a user, sorted by recency."""
        tasks = [
            t for t in self._tasks.values()
            if t.user_id == user_id and (include_terminal or not t.is_terminal)
        ]
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        return tasks[:limit]

    def get_tasks_by_type(
        self,
        user_id: str,
        task_type: str,
        limit: int = 10,
    ) -> List[TaskState]:
        """Get tasks of a specific type for pattern analysis."""
        tasks = [
            t for t in self._tasks.values()
            if t.user_id == user_id and t.task_type == task_type
        ]
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        return tasks[:limit]

    def update_task(self, task: TaskState) -> None:
        """Persist task changes."""
        task.updated_at = time.time()
        self._save_task(task)

    def get_plan_success_rate(self, user_id: str, task_type: str) -> Dict[str, Any]:
        """Analyze plan success rates for a task type — feeds into policy learning."""
        tasks = self.get_tasks_by_type(user_id, task_type, limit=50)
        completed = [t for t in tasks if t.status == TaskStatus.COMPLETED]
        failed = [t for t in tasks if t.status == TaskStatus.FAILED]

        if not completed and not failed:
            return {"task_type": task_type, "samples": 0}

        total = len(completed) + len(failed)
        success_rate = len(completed) / total if total > 0 else 0.0

        # Analyze which plan patterns lead to success
        successful_steps = []
        for t in completed:
            step_descs = [s.description.lower() for s in t.plan if s.status == StepStatus.COMPLETED]
            successful_steps.extend(step_descs)

        failed_steps = []
        for t in failed:
            for s in t.plan:
                if s.status == StepStatus.FAILED:
                    failed_steps.append(s.description.lower())

        return {
            "task_type": task_type,
            "samples": total,
            "success_rate": round(success_rate, 3),
            "avg_steps_successful": (
                sum(len(t.plan) for t in completed) / len(completed)
                if completed else 0
            ),
            "common_successful_steps": _top_n_words(successful_steps, 10),
            "common_failure_points": _top_n_words(failed_steps, 5),
        }

    def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        tasks = list(self._tasks.values())
        if user_id:
            tasks = [t for t in tasks if t.user_id == user_id]

        by_status = {}
        for t in tasks:
            by_status[t.status.value] = by_status.get(t.status.value, 0) + 1

        return {
            "total": len(tasks),
            "by_status": by_status,
            "active": len(self._active_tasks),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_task(self, task: TaskState) -> None:
        path = os.path.join(self._dir, f"{task.id}.json")
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(task.to_dict(), f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as e:
            logger.debug("Failed to save task %s: %s", task.id, e)

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
                task = TaskState.from_dict(data)
                self._tasks[task.id] = task
                if not task.is_terminal:
                    self._active_tasks[task.user_id] = task.id
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.debug("Failed to load task %s: %s", fname, e)

    def flush(self) -> None:
        for task in self._tasks.values():
            self._save_task(task)


def _top_n_words(texts: List[str], n: int) -> List[str]:
    """Extract top N significant words from a list of texts."""
    freq: Dict[str, int] = {}
    stop = {"the", "a", "an", "to", "of", "in", "for", "on", "and", "or", "is", "it", "with"}
    for text in texts:
        for word in text.split():
            if len(word) > 2 and word not in stop:
                freq[word] = freq.get(word, 0) + 1
    sorted_words = sorted(freq, key=freq.get, reverse=True)
    return sorted_words[:n]
