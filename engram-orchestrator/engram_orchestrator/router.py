"""TaskRouter — auto-routes tasks to agents via semantic capability matching.

Routing algorithm:
1. Build query from task title + description + tags
2. Semantic search over agent capability memories
3. Filter by availability and capacity
4. Score: similarity * 0.7 + availability * 0.3
5. Assign top agent to task
"""

from __future__ import annotations

import logging
from typing import Any

from engram_orchestrator.registry import AgentRegistry

logger = logging.getLogger(__name__)


class TaskRouter:
    """Routes tasks to agents based on semantic capability matching."""

    def __init__(self, registry: AgentRegistry, task_manager: Any) -> None:
        self._registry = registry
        self._tm = task_manager

    def route(self, task_id: str, *, force: bool = False) -> dict | None:
        """Route a single task to the best available agent.

        Returns the updated task dict, or None if no suitable agent found.
        If the task is already assigned and force=False, returns it as-is.
        """
        task = self._tm.get_task(task_id)
        if not task:
            logger.warning("Task '%s' not found for routing", task_id)
            return None

        # Already assigned?
        if task.get("assigned_agent") and not force:
            return task

        # Build semantic query from task content
        title = task.get("title", "")
        description = task.get("description", "")
        tags = task.get("tags", [])
        query = f"{title}. {description} {' '.join(tags)}".strip()

        if not query:
            logger.warning("Task '%s' has no content for routing", task_id)
            return None

        # Semantic search over agent capabilities
        candidates = self._registry.find_capable(query, limit=5)
        if not candidates:
            logger.info("No agents found for task '%s'", task_id)
            return None

        # Filter and score
        best_agent = None
        best_score = -1.0

        for agent in candidates:
            # Must be available
            if agent.get("status") != "available":
                continue

            # Must have capacity
            active_count = len(agent.get("active_tasks", []))
            max_concurrent = agent.get("max_concurrent", 1)
            if active_count >= max_concurrent:
                continue

            # Score: similarity * 0.7 + availability_ratio * 0.3
            similarity = agent.get("similarity", 0.0)
            availability = 1.0 - (active_count / max_concurrent) if max_concurrent > 0 else 0.0
            score = similarity * 0.7 + availability * 0.3

            if score > best_score:
                best_score = score
                best_agent = agent

        if not best_agent:
            logger.info("No available agent for task '%s' (all busy or at capacity)", task_id)
            return None

        agent_name = best_agent["name"]

        # Update task: assign agent and set status to "assigned"
        updated = self._tm.update_task(task_id, {
            "assigned_agent": agent_name,
            "status": "assigned",
        })

        # Update agent: add to active tasks
        self._registry.add_active_task(agent_name, task_id)

        logger.info(
            "Routed task '%s' (%s) → %s (score: %.2f)",
            task_id, title, agent_name, best_score,
        )

        return updated

    def route_pending(self) -> list[dict]:
        """Batch-route all unassigned tasks. Returns list of routed tasks."""
        tasks = self._tm.list_tasks(user_id="bridge", status="inbox", limit=50)
        routed = []
        for task in tasks:
            if not task.get("assigned_agent"):
                result = self.route(task["id"])
                if result and result.get("assigned_agent"):
                    routed.append(result)
        return routed

    def unassign(self, task_id: str) -> dict | None:
        """Unassign a task from its current agent."""
        task = self._tm.get_task(task_id)
        if not task:
            return None

        agent_name = task.get("assigned_agent")
        if agent_name:
            self._registry.remove_active_task(agent_name, task_id)

        updated = self._tm.update_task(task_id, {
            "assigned_agent": None,
            "status": "inbox",
        })
        return updated
