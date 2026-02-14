"""Coordinator — top-level orchestrator tying registry + router + bus events.

Subscribes to bus events for automatic task routing. Provides CAS-style
claim/release for agent task ownership.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from engram_orchestrator.registry import AgentRegistry
from engram_orchestrator.router import TaskRouter

logger = logging.getLogger(__name__)

# Default capability profiles per agent type
_DEFAULT_CAPS: dict[str, dict] = {
    "claude": {
        "capabilities": ["python", "typescript", "debugging", "code-review", "refactoring", "testing"],
        "description": "Advanced coding agent. Expert at Python, TypeScript, debugging, and code review.",
    },
    "codex": {
        "capabilities": ["python", "javascript", "scaffolding", "prototyping", "code-generation"],
        "description": "Fast code generation agent. Good at scaffolding, prototyping, and quick edits.",
    },
    "custom": {
        "capabilities": ["general"],
        "description": "Custom agent with general capabilities.",
    },
}


class Coordinator:
    """Memory-based task coordination layer.

    Ties together the AgentRegistry (capabilities as memories),
    TaskRouter (semantic matching), and Bus (event-driven routing).
    """

    def __init__(self, memory: Any, bus: Any, config: Any) -> None:
        self._memory = memory
        self._bus = bus
        self._config = config

        from engram.memory.tasks import TaskManager
        self._tm = TaskManager(memory)

        self.registry = AgentRegistry(memory, user_id="system")
        self.router = TaskRouter(self.registry, self._tm)

        self._subscribed = False

    # ── Lifecycle ──

    def start(self) -> None:
        """Subscribe to bus events for auto-routing."""
        if self._config.auto_route:
            self._bus.subscribe("bridge.task.created", self._on_bus_task_created)
            self._subscribed = True
            logger.info("Coordinator started (auto_route=True)")
        else:
            logger.info("Coordinator started (auto_route=False, manual routing only)")

    def stop(self) -> None:
        """Unsubscribe from bus events."""
        if self._subscribed:
            self._bus.unsubscribe("bridge.task.created", self._on_bus_task_created)
            self._subscribed = False
        logger.info("Coordinator stopped")

    # ── Agent Registration ──

    def register_from_config(
        self,
        agents: dict[str, Any],
        caps_map: dict[str, dict] | None = None,
    ) -> None:
        """Register agents from bridge config with capability profiles.

        Args:
            agents: dict of agent_name -> AgentConfig from bridge config
            caps_map: optional override map of agent_name -> {capabilities, description}
        """
        caps_map = caps_map or {}

        for name, acfg in agents.items():
            # Look up capabilities: explicit map > config defaults > type defaults
            if name in caps_map:
                caps_info = caps_map[name]
            elif name in self._config.default_capabilities:
                caps_info = self._config.default_capabilities[name]
            else:
                caps_info = _DEFAULT_CAPS.get(acfg.type, _DEFAULT_CAPS["custom"])

            self.registry.register(
                name,
                capabilities=caps_info.get("capabilities", ["general"]),
                description=caps_info.get("description", f"{name} agent"),
                agent_type=acfg.type,
                model=acfg.model or "",
                max_concurrent=caps_info.get("max_concurrent", 1),
            )
            logger.info("Registered agent '%s' (type=%s)", name, acfg.type)

    # ── Task Operations ──

    def on_task_created(self, task_data: dict) -> dict | None:
        """Handle a newly created task — auto-route if enabled."""
        task_id = task_data.get("task_id") or task_data.get("id", "")
        if not task_id:
            return None

        if not self._config.auto_route:
            return None

        result = self.router.route(task_id)

        if result and result.get("assigned_agent"):
            self.log_event("task_routed", {
                "task_id": task_id,
                "title": result.get("title", ""),
                "agent": result["assigned_agent"],
            })
            # Publish routed event on bus
            self._bus.publish("bridge.task.routed", {
                "task_id": task_id,
                "agent": result["assigned_agent"],
                "title": result.get("title", ""),
            })

        return result

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
            self.registry.add_active_task(agent_name, task_id)
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

        self.registry.remove_active_task(agent_name, task_id)
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
        if not self._config.log_events:
            return

        now = datetime.now(timezone.utc).isoformat()

        # Build human-readable content
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

    # ── Bus Callback ──

    def _on_bus_task_created(self, topic: str, data: Any, agent_id: str | None) -> None:
        """Bus callback for bridge.task.created events."""
        try:
            self.on_task_created(data)
        except Exception as e:
            logger.error("Error in auto-route for task: %s", e)
