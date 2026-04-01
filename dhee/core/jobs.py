"""Dhee v3 — Job Registry: named, idempotent, observable maintenance jobs.

Replaces agi_loop.py's phantom subsystems with real, independently testable jobs.

Each job:
    - Has a unique name (e.g., "distill_episodic_to_semantic")
    - Is idempotent: same input → same output, safe to retry
    - Is observable: status, timing, retry count tracked in maintenance_jobs table
    - Is leasable: acquires a lock before running (via LeaseManager)
    - Returns a structured result dict

Design contract:
    - Jobs NEVER call memory.add() or any write-path that triggers enrichment
    - Jobs write to derived stores + lineage only
    - Jobs are cold-path: called by heartbeat/cron, never by hot-path remember/recall
"""

from __future__ import annotations

import json
import logging
import traceback
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

from dhee.core.lease_manager import LeaseManager

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Job(ABC):
    """Base class for all maintenance jobs."""

    # Subclasses MUST set this
    name: str = ""

    def __init__(self):
        if not self.name:
            raise ValueError(f"{self.__class__.__name__} must set 'name'")

    @abstractmethod
    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run the job. Returns a result dict.

        Args:
            payload: Job-specific input parameters

        Returns:
            Dict with job results (stored in maintenance_jobs.result_json)

        Raises:
            Exception on failure (caught by JobRegistry, stored as error)
        """
        ...

    def make_idempotency_key(self, payload: Dict[str, Any]) -> Optional[str]:
        """Generate an idempotency key for dedup. Override if needed.

        Returns None to skip idempotency checking (every run creates a new job).
        """
        return None


class JobRegistry:
    """Manages job registration, scheduling, execution, and observability.

    Usage:
        registry = JobRegistry(conn, lock, lease_manager)
        registry.register(ApplyForgettingJob)
        registry.register(DistillEpisodicJob)

        # Run a specific job
        result = registry.run("apply_forgetting", payload={"user_id": "u1"})

        # Run all due jobs (heartbeat)
        results = registry.run_all(owner_id="worker-1")
    """

    def __init__(
        self,
        conn: "sqlite3.Connection",
        lock: "threading.RLock",
        lease_manager: LeaseManager,
    ):
        import sqlite3
        import threading

        self._conn = conn
        self._lock = lock
        self._lease = lease_manager
        self._jobs: Dict[str, Job] = {}

    def register(self, job_class: Type[Job]) -> None:
        """Register a job class. Instantiates it."""
        job = job_class()
        if job.name in self._jobs:
            logger.warning("Job %s already registered, replacing", job.name)
        self._jobs[job.name] = job

    def list_registered(self) -> List[str]:
        """List all registered job names."""
        return list(self._jobs.keys())

    def run(
        self,
        job_name: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        owner_id: str = "default-worker",
    ) -> Dict[str, Any]:
        """Run a single job by name. Acquires lease, executes, records result.

        Returns:
            Dict with: job_id, job_name, status, result/error, timing
        """
        if job_name not in self._jobs:
            return {
                "job_name": job_name,
                "status": "error",
                "error": f"Unknown job: {job_name}",
            }

        job = self._jobs[job_name]
        payload = payload or {}

        # Idempotency check
        idem_key = job.make_idempotency_key(payload)
        if idem_key:
            with self._lock:
                existing = self._conn.execute(
                    """SELECT job_id, status, result_json FROM maintenance_jobs
                       WHERE idempotency_key = ? AND status IN ('completed', 'running')
                       LIMIT 1""",
                    (idem_key,),
                ).fetchone()
                if existing:
                    return {
                        "job_id": existing["job_id"],
                        "job_name": job_name,
                        "status": "skipped_idempotent",
                        "existing_status": existing["status"],
                    }

        # Acquire lease
        lock_id = f"job:{job_name}"
        if not self._lease.acquire(lock_id, owner_id):
            return {
                "job_name": job_name,
                "status": "skipped_locked",
                "holder": self._lease.get_holder(lock_id),
            }

        # Create job record
        job_id = str(uuid.uuid4())
        now = _utcnow_iso()

        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO maintenance_jobs
                       (job_id, job_name, status, payload_json,
                        created_at, started_at, idempotency_key)
                       VALUES (?, ?, 'running', ?, ?, ?, ?)""",
                    (
                        job_id, job_name, json.dumps(payload),
                        now, now, idem_key,
                    ),
                )
                self._conn.commit()

            # Execute
            result = job.execute(payload)
            completed_at = _utcnow_iso()

            with self._lock:
                self._conn.execute(
                    """UPDATE maintenance_jobs
                       SET status = 'completed', result_json = ?,
                           completed_at = ?
                       WHERE job_id = ?""",
                    (json.dumps(result, default=str), completed_at, job_id),
                )
                self._conn.commit()

            return {
                "job_id": job_id,
                "job_name": job_name,
                "status": "completed",
                "result": result,
                "started_at": now,
                "completed_at": completed_at,
            }

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.exception("Job %s failed: %s", job_name, error_msg)

            with self._lock:
                self._conn.execute(
                    """UPDATE maintenance_jobs
                       SET status = 'failed', error_message = ?,
                           completed_at = ?,
                           retry_count = retry_count + 1
                       WHERE job_id = ?""",
                    (error_msg, _utcnow_iso(), job_id),
                )
                self._conn.commit()

            return {
                "job_id": job_id,
                "job_name": job_name,
                "status": "failed",
                "error": error_msg,
            }

        finally:
            self._lease.release(lock_id, owner_id)

    def run_all(
        self, *, owner_id: str = "default-worker"
    ) -> List[Dict[str, Any]]:
        """Run all registered jobs. Returns list of results."""
        results = []
        for name in self._jobs:
            result = self.run(name, owner_id=owner_id)
            results.append(result)
        return results

    def get_job_history(
        self,
        job_name: str,
        *,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get recent execution history for a job."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT job_id, job_name, status, payload_json,
                          result_json, error_message,
                          created_at, started_at, completed_at,
                          retry_count
                   FROM maintenance_jobs
                   WHERE job_name = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (job_name, limit),
            ).fetchall()

        return [
            {
                "job_id": r["job_id"],
                "job_name": r["job_name"],
                "status": r["status"],
                "payload": json.loads(r["payload_json"] or "{}"),
                "result": json.loads(r["result_json"] or "null"),
                "error": r["error_message"],
                "created_at": r["created_at"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
                "retry_count": r["retry_count"],
            }
            for r in rows
        ]

    def get_health(self) -> Dict[str, Any]:
        """Get health summary across all registered jobs."""
        health: Dict[str, Any] = {
            "registered_jobs": list(self._jobs.keys()),
            "total_registered": len(self._jobs),
            "job_status": {},
        }

        for name in self._jobs:
            with self._lock:
                row = self._conn.execute(
                    """SELECT status, completed_at, error_message
                       FROM maintenance_jobs
                       WHERE job_name = ?
                       ORDER BY created_at DESC
                       LIMIT 1""",
                    (name,),
                ).fetchone()

            if row:
                health["job_status"][name] = {
                    "last_status": row["status"],
                    "last_completed": row["completed_at"],
                    "last_error": row["error_message"],
                }
            else:
                health["job_status"][name] = {"last_status": "never_run"}

        return health


# =========================================================================
# Concrete Job Implementations
# =========================================================================

class ApplyForgettingJob(Job):
    """Apply decay/forgetting curves to memory strengths.

    Replaces agi_loop step 2 (decay).
    """

    name = "apply_forgetting"

    def __init__(self):
        super().__init__()
        self._memory = None  # Set by caller via set_context()

    def set_context(self, memory: Any) -> "ApplyForgettingJob":
        self._memory = memory
        return self

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._memory:
            return {"status": "skipped", "reason": "no memory instance"}

        user_id = payload.get("user_id", "default")
        try:
            result = self._memory.apply_decay(scope={"user_id": user_id})
            return {"status": "ok", "decay_result": result}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class RunConsolidationJob(Job):
    """Run the cognition kernel's sleep_cycle (distillation).

    Replaces agi_loop step 1 (consolidate).
    """

    name = "run_consolidation"

    def __init__(self):
        super().__init__()
        self._kernel = None

    def set_context(self, kernel: Any) -> "RunConsolidationJob":
        self._kernel = kernel
        return self

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._kernel:
            return {"status": "skipped", "reason": "no kernel instance"}

        user_id = payload.get("user_id", "default")
        try:
            result = self._kernel.sleep_cycle(user_id=user_id)
            return {"status": "ok", "consolidation_result": result}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class ExtractStepPoliciesJob(Job):
    """Extract step-level policies from completed tasks.

    Replaces the inline policy extraction in record_learning_outcomes.
    """

    name = "extract_step_policies"

    def __init__(self):
        super().__init__()
        self._kernel = None

    def set_context(self, kernel: Any) -> "ExtractStepPoliciesJob":
        self._kernel = kernel
        return self

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._kernel:
            return {"status": "skipped", "reason": "no kernel instance"}

        user_id = payload.get("user_id", "default")
        task_id = payload.get("task_id")

        if not task_id:
            return {"status": "skipped", "reason": "no task_id in payload"}

        try:
            # Look up the task
            task = self._kernel.task_manager.get(task_id)
            if not task:
                return {"status": "skipped", "reason": f"task {task_id} not found"}

            # Use existing policy extraction
            policies_created = 0
            if hasattr(self._kernel, 'policy_manager') and self._kernel.policy_manager:
                from dhee.core.task_state import TaskStatus
                if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    # Extract via existing mechanisms
                    policies_created = self._kernel.policy_manager.extract_from_task(
                        task, user_id=user_id
                    ) if hasattr(self._kernel.policy_manager, 'extract_from_task') else 0

            return {"status": "ok", "policies_created": policies_created}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def make_idempotency_key(self, payload: Dict[str, Any]) -> Optional[str]:
        task_id = payload.get("task_id", "")
        return f"step_policies:{task_id}" if task_id else None


class DetectFailurePatternsJob(Job):
    """Run the FailurePatternDetector on terminal tasks.

    Replaces the inline pattern detection in record_learning_outcomes.
    """

    name = "detect_failure_patterns"

    def __init__(self):
        super().__init__()
        self._kernel = None

    def set_context(self, kernel: Any) -> "DetectFailurePatternsJob":
        self._kernel = kernel
        return self

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._kernel:
            return {"status": "skipped", "reason": "no kernel instance"}

        user_id = payload.get("user_id", "default")
        try:
            from dhee.core.pattern_detector import (
                FailurePatternDetector, extract_features,
            )
            from dhee.core.task_state import TaskStatus

            # Get terminal tasks
            all_tasks = self._kernel.task_manager.list_tasks(
                user_id=user_id, limit=200
            )
            terminal = [
                t for t in all_tasks
                if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            ]

            if len(terminal) < 10:
                return {
                    "status": "ok", "patterns_found": 0,
                    "reason": f"only {len(terminal)} terminal tasks (need 10+)",
                }

            features = extract_features(terminal)
            detector = FailurePatternDetector()
            patterns = detector.detect_and_describe(features)

            stored = 0
            for pattern in patterns:
                if hasattr(self._kernel, '_store_pattern_as_policy'):
                    policy = self._kernel._store_pattern_as_policy(
                        user_id, "detected", pattern,
                    )
                    if policy:
                        stored += 1

            return {
                "status": "ok",
                "terminal_tasks": len(terminal),
                "patterns_found": len(patterns),
                "patterns_stored": stored,
            }
        except ImportError:
            return {"status": "skipped", "reason": "pattern_detector not available"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
