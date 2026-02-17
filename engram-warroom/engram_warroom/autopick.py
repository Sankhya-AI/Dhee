"""Auto-pick top priority task and dispatch to an agent."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Priority ordering (highest first)
_PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "normal": 3, "low": 4}


class AutoPicker:
    """Auto-pick the highest priority pending task and dispatch it."""

    def __init__(self, memory: Any, bus: Any, router: Any = None) -> None:
        self._memory = memory
        self._bus = bus
        self._router = router  # Optional TaskRouter for agent selection

    def pick_top_task(self, user_id: str = "bridge") -> dict[str, Any] | None:
        """Find the highest-priority pending/inbox task.

        Sort: priority (urgent > high > medium > normal > low),
        then created_at ASC (FIFO within same priority).
        """
        from engram.memory.tasks import TaskManager
        tm = TaskManager(self._memory)

        pending = tm.get_pending_tasks(user_id=user_id)
        if not pending:
            return None

        def sort_key(task: dict) -> tuple:
            priority = task.get("priority", "normal")
            rank = _PRIORITY_ORDER.get(priority, 3)
            created = task.get("created_at", "")
            return (rank, created)

        pending.sort(key=sort_key)
        return pending[0]

    def pick_and_dispatch(
        self,
        user_id: str = "bridge",
        agent_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Pick top task, update to 'active', assign agent, publish event.

        If agent_name is not provided and a router is available,
        the router will select the best agent.
        """
        task = self.pick_top_task(user_id=user_id)
        if not task:
            return None

        task_id = task.get("id", "")
        if not task_id:
            return None

        from engram.memory.tasks import TaskManager
        tm = TaskManager(self._memory)

        # If no agent specified, try router
        if not agent_name and self._router:
            route_result = self._router.route(task_id)
            if route_result:
                agent_name = route_result.get("assigned_agent")

        # Update task status to active
        update_data: dict[str, Any] = {"status": "active"}
        if agent_name:
            update_data["assigned_agent"] = agent_name
        tm.update_task(task_id, update_data)

        result = {
            "task_id": task_id,
            "title": task.get("title", ""),
            "description": task.get("description", ""),
            "priority": task.get("priority", "normal"),
            "agent": agent_name or "",
        }

        if self._bus:
            self._bus.publish("warroom.auto_picked", result)
            # Also publish the execution event for the bridge
            self._bus.publish("bridge.task.execute", {
                "task_id": task_id,
                "agent": agent_name or "",
                "title": task.get("title", ""),
                "description": task.get("description", ""),
            })

        logger.info(
            "Auto-picked task %s [%s] %s -> %s",
            task_id, result["priority"], result["title"], agent_name or "unassigned",
        )
        return result
