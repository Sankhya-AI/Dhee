"""Progress tracking across sub-tasks."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Track progress of sub-tasks for a parent task."""

    def __init__(self, task_manager: Any) -> None:
        self._tm = task_manager

    def get_subtasks(self, parent_task_id: str) -> list[dict]:
        """Get all sub-tasks for a parent task."""
        # Search by parent_task_id in task fields
        results = self._tm.list_tasks(user_id="default", limit=100)
        return [
            t for t in results
            if t.get("parent_task_id") == parent_task_id
            or t.get("custom", {}).get("parent_task_id") == parent_task_id
        ]

    def track(self, parent_task_id: str) -> dict:
        """Get progress summary: how many sub-tasks done, active, blocked."""
        subtasks = self.get_subtasks(parent_task_id)

        done = sum(1 for t in subtasks if t.get("status") in ("done", "completed"))
        active = sum(1 for t in subtasks if t.get("status") in ("active", "assigned"))
        blocked = sum(1 for t in subtasks if t.get("status") == "blocked")
        pending = sum(1 for t in subtasks if t.get("status") in ("inbox", "pending"))
        total = len(subtasks)

        return {
            "parent_task_id": parent_task_id,
            "total": total,
            "done": done,
            "active": active,
            "blocked": blocked,
            "pending": pending,
            "progress_pct": round(done / total * 100, 1) if total > 0 else 0.0,
            "is_complete": done == total and total > 0,
        }

    def aggregate(self, parent_task_id: str) -> dict:
        """Collect results from completed sub-tasks into a summary."""
        subtasks = self.get_subtasks(parent_task_id)
        results = []
        for t in subtasks:
            if t.get("status") in ("done", "completed"):
                results.append({
                    "title": t.get("title", ""),
                    "result": t.get("result", t.get("description", "")),
                    "assigned_agent": t.get("assigned_agent", ""),
                })
        return {
            "parent_task_id": parent_task_id,
            "completed_subtasks": len(results),
            "results": results,
        }

    def is_complete(self, parent_task_id: str) -> bool:
        """Check if all sub-tasks are done."""
        progress = self.track(parent_task_id)
        return progress["is_complete"]

    def cancel(self, parent_task_id: str) -> int:
        """Cancel all incomplete sub-tasks. Returns count cancelled."""
        subtasks = self.get_subtasks(parent_task_id)
        count = 0
        for t in subtasks:
            if t.get("status") not in ("done", "completed", "archived"):
                try:
                    self._tm.update_task(t["id"], {"status": "archived"})
                    count += 1
                except Exception as e:
                    logger.warning("Failed to cancel subtask %s: %s", t.get("id"), e)
        return count
