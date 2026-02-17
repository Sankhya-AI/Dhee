"""TaskRouter — auto-routes tasks to agents via semantic capability matching.

Routing algorithm:
1. Build query from task title + description + tags
2. Semantic search over agent capability memories
3. Filter by availability and capacity
4. Score: similarity * weight + availability * weight
5. Assign top agent to task

Claim/release operations provide atomic CAS-style task ownership.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from engram_router.registry import AgentRegistry

logger = logging.getLogger(__name__)


class TaskRouter:
    """Routes tasks to agents based on semantic capability matching.

    Also provides CAS-style claim/release for agent task ownership,
    replacing the old Coordinator class.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        task_manager: Any,
        *,
        config: Any = None,
        memory: Any = None,
    ) -> None:
        self._registry = registry
        self._tm = task_manager
        self._config = config
        self._memory = memory
        self._bus = None

        # Weights from config or defaults
        if config:
            self._sim_weight = getattr(config, "similarity_weight", 0.7)
            self._avail_weight = getattr(config, "availability_weight", 0.3)
            self._log_events = getattr(config, "log_events", True)
        else:
            self._sim_weight = 0.7
            self._avail_weight = 0.3
            self._log_events = True

    def connect_bus(self, bus: Any) -> None:
        """Optionally connect a bus for event publishing."""
        self._bus = bus

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

            # Score: similarity * weight + availability_ratio * weight
            similarity = agent.get("similarity", 0.0)
            availability = 1.0 - (active_count / max_concurrent) if max_concurrent > 0 else 0.0
            score = similarity * self._sim_weight + availability * self._avail_weight

            if score > best_score:
                best_score = score
                best_agent = agent

        # Fallback to default supervisor agent if no semantic match
        fallback = getattr(self._config, "fallback_agent", "") if self._config else ""
        if not best_agent and fallback:
            logger.info("No semantic match for task '%s', falling back to '%s'", task_id, fallback)
            agent_name = fallback
        elif not best_agent:
            logger.info("No available agent for task '%s' (no match and no fallback)", task_id)
            return None
        else:
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

        # Log event and publish to bus
        self.log_event("task_routed", {
            "task_id": task_id,
            "title": title,
            "agent": agent_name,
        })
        if self._bus:
            self._bus.publish("bridge.task.routed", {
                "task_id": task_id,
                "agent": agent_name,
                "title": title,
            })

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

    # ── Claim / Release (moved from Coordinator) ──

    def claim(self, task_id: str, agent_name: str) -> dict | None:
        """Atomic CAS claim — agent claims a task if it's unassigned or assigned to them.

        Returns the updated task, or None if claim fails (already claimed by another).
        """
        task = self._tm.get_task(task_id)
        if not task:
            return None

        current_status = task.get("status", "")
        current_agent = task.get("assigned_agent")

        # CAS check: only claim if inbox/assigned and no conflicting assignee
        if current_status not in ("inbox", "assigned"):
            logger.info("Claim denied: task '%s' status is '%s'", task_id, current_status)
            return None

        if current_agent and current_agent != agent_name:
            logger.info(
                "Claim denied: task '%s' already assigned to '%s'", task_id, current_agent,
            )
            return None

        # Perform the claim
        updated = self._tm.update_task(task_id, {
            "assigned_agent": agent_name,
            "status": "active",
        })

        if updated:
            self._registry.add_active_task(agent_name, task_id)
            self.log_event("task_claimed", {
                "task_id": task_id,
                "title": task.get("title", ""),
                "agent": agent_name,
            })

        return updated

    def release(self, task_id: str, agent_name: str) -> dict | None:
        """Release a task back to the pool."""
        task = self._tm.get_task(task_id)
        if not task:
            return None

        if task.get("assigned_agent") != agent_name:
            logger.info("Release denied: task '%s' not owned by '%s'", task_id, agent_name)
            return None

        self._registry.remove_active_task(agent_name, task_id)
        updated = self._tm.update_task(task_id, {
            "assigned_agent": None,
            "status": "inbox",
        })

        if updated:
            self.log_event("task_released", {
                "task_id": task_id,
                "title": task.get("title", ""),
                "agent": agent_name,
            })

        return updated

    # ── Event Logging ──

    def log_event(self, event_type: str, details: dict) -> None:
        """Store a coordination event as a memory."""
        if not self._log_events:
            return
        if not self._memory:
            return

        now = datetime.now(timezone.utc).isoformat()
        title = details.get("title", "")
        agent = details.get("agent", "")
        task_id = details.get("task_id", "")

        if event_type == "task_routed":
            content = f"[task_routed] '{title}' → {agent}"
        elif event_type == "task_claimed":
            content = f"[task_claimed] '{title}' claimed by {agent}"
        elif event_type == "task_released":
            content = f"[task_released] '{title}' released by {agent}"
        else:
            content = f"[{event_type}] task={task_id} agent={agent}"

        try:
            self._memory.add(
                content,
                user_id="system",
                metadata={
                    "memory_type": "coordination_event",
                    "coord_event_type": event_type,
                    "coord_timestamp": now,
                    "coord_details": details,
                },
                categories=["coordination/events"],
                infer=False,
            )
        except Exception as e:
            logger.warning("Failed to log coordination event: %s", e)
