"""Coordination layer — wraps engram-router for backward compatibility.

Provides a Coordinator class that the Bridge and WebChannel expect,
backed by engram-router's AgentRegistry + TaskRouter.
"""

from __future__ import annotations

import logging
from typing import Any

from engram_router import AgentRegistry, TaskRouter, RouterConfig

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
    """Compatibility wrapper providing the old Coordinator API.

    Uses engram-router's AgentRegistry + TaskRouter under the hood.
    No central orchestrator agent — memory IS the orchestrator.
    """

    def __init__(self, memory: Any, bus: Any, config: Any, default_agent: str = "") -> None:
        self._memory = memory
        self._bus = bus
        self._config = config

        from engram.memory.tasks import TaskManager
        tm = TaskManager(memory)

        router_config = RouterConfig(
            auto_route=getattr(config, "auto_route", True),
            auto_execute=getattr(config, "auto_execute", False),
            log_events=getattr(config, "log_events", True),
            fallback_agent=default_agent,
        )

        self.registry = AgentRegistry(memory, user_id=router_config.user_id)
        self.router = TaskRouter(self.registry, tm, config=router_config, memory=memory)
        self.router.connect_bus(bus)

        self._subscribed = False

    def register_from_config(
        self,
        agents: dict[str, Any],
        caps_map: dict[str, dict] | None = None,
    ) -> None:
        """Register agents from bridge config with capability profiles."""
        caps_map = caps_map or {}

        for name, acfg in agents.items():
            if name in caps_map:
                caps_info = caps_map[name]
            elif hasattr(self._config, "default_capabilities") and name in (self._config.default_capabilities or {}):
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

    def start(self) -> None:
        """Subscribe to bus events for auto-routing."""
        if getattr(self._config, "auto_route", True):
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

    def claim(self, task_id: str, agent_name: str) -> dict | None:
        """Delegate to TaskRouter.claim()."""
        return self.router.claim(task_id, agent_name)

    def on_task_created(self, task_data: dict) -> dict | None:
        """Handle a newly created task — auto-route if enabled."""
        task_id = task_data.get("task_id") or task_data.get("id", "")
        if not task_id:
            return None

        if not getattr(self._config, "auto_route", True):
            return None

        result = self.router.route(task_id)

        if result and result.get("assigned_agent"):
            if getattr(self._config, "auto_execute", False):
                self._bus.publish("bridge.task.execute", {
                    "task_id": task_id,
                    "agent": result["assigned_agent"],
                    "title": result.get("title", ""),
                    "description": result.get("description", ""),
                })

        return result

    def _on_bus_task_created(self, topic: str, data: Any, agent_id: str | None = None) -> None:
        """Bus callback for bridge.task.created events."""
        try:
            self.on_task_created(data)
        except Exception as e:
            logger.error("Error in auto-route for task: %s", e)


__all__ = ["AgentRegistry", "TaskRouter", "RouterConfig", "Coordinator"]
