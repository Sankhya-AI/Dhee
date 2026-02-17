"""Prospective memory — remembering the future.

All intentions are stored as Engram memories with `memory_type="prospective"`.
No new DB tables.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from engram_prospective.config import ProspectiveConfig
from engram_prospective.triggers import evaluate_trigger, is_expired

logger = logging.getLogger(__name__)


class Prospective:
    """Prospective memory engine — the Planner.

    Provides:
    - Intention CRUD (add, list, complete, cancel)
    - Trigger evaluation (time, event, condition)
    - Due intention queries
    """

    def __init__(
        self,
        memory: Any,
        user_id: str = "default",
        config: Optional[ProspectiveConfig] = None,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self.config = config or ProspectiveConfig()

    def add_intention(
        self,
        description: str,
        trigger_type: str,
        trigger_value: str,
        action: Optional[str] = None,
        priority: Optional[int] = None,
        agent_id: Optional[str] = None,
        context: Optional[str] = None,
        expiry: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remember something to do later.

        Args:
            description: What needs to be done.
            trigger_type: One of "time", "event", "condition".
            trigger_value: Trigger specification:
                - time: ISO datetime string (e.g. "2025-01-20T09:00:00Z")
                - event: Event name (e.g. "deploy_complete")
                - condition: Condition expression (e.g. "status=ready")
            action: What to do when triggered (optional detail).
            priority: 1 (highest) to 10 (lowest). Default: 5.
            agent_id: Which agent owns this intention.
            context: Additional context for the intention.
            expiry: ISO datetime when this intention expires if not triggered.
        """
        valid_types = {"time", "event", "condition"}
        if trigger_type not in valid_types:
            return {"error": f"Invalid trigger_type. Must be one of: {valid_types}"}

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Default expiry based on config
        if not expiry:
            expiry = (now + timedelta(days=self.config.intention_expiry_days)).isoformat()

        result = self.memory.add(
            messages=f"Intention: {description}",
            user_id=self.user_id,
            metadata={
                "memory_type": "prospective",
                "pm_agent_id": agent_id or "",
                "pm_description": description,
                "pm_trigger_type": trigger_type,
                "pm_trigger_value": trigger_value,
                "pm_priority": priority or self.config.default_priority,
                "pm_status": "pending",
                "pm_action": action or description,
                "pm_context": context or "",
                "pm_created_at": now_iso,
                "pm_triggered_at": None,
                "pm_completed_at": None,
                "pm_expiry": expiry,
            },
        )

        results_list = result.get("results", [result])
        intention_id = results_list[0].get("id") if results_list else None

        return {
            "action": "created",
            "intention_id": intention_id,
            "description": description,
            "trigger_type": trigger_type,
            "trigger_value": trigger_value,
            "priority": priority or self.config.default_priority,
            "expiry": expiry,
        }

    def list_intentions(
        self,
        status: str = "pending",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List intentions filtered by status."""
        results = self.memory.search(
            query="intention prospective",
            user_id=self.user_id,
            filters={"memory_type": "prospective", "pm_status": status},
            limit=limit,
        )

        intentions = []
        for mem in results.get("results", []):
            metadata = mem.get("metadata", {}) or {}
            intentions.append(self._format_intention(mem, metadata))

        # Sort by priority (lower = higher priority)
        intentions.sort(key=lambda x: x.get("priority", 10))
        return intentions

    def check_triggers(
        self,
        events: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate all pending intentions and return those whose triggers fired.

        This is the core prospective memory operation. Call it periodically
        (via heartbeat) or manually.

        Args:
            events: Dict of recent events for event-trigger matching.
            context: Dict of current context for condition-trigger matching.

        Returns:
            List of triggered intentions (status changed to "triggered").
        """
        pending = self.memory.search(
            query="intention prospective pending",
            user_id=self.user_id,
            filters={"memory_type": "prospective", "pm_status": "pending"},
            limit=self.config.max_intentions_per_user,
        )

        now = datetime.now(timezone.utc)
        triggered = []

        for mem in pending.get("results", []):
            metadata = mem.get("metadata", {}) or {}

            # Check expiry first
            if is_expired(mem, self.config):
                self._update_status(mem["id"], "expired")
                continue

            # Evaluate trigger
            if evaluate_trigger(
                intention=mem,
                current_time=now,
                events=events,
                context=context,
                config=self.config,
            ):
                self._update_status(mem["id"], "triggered", triggered_at=now.isoformat())
                triggered.append(self._format_intention(mem, metadata, status_override="triggered"))

        return triggered

    def complete_intention(
        self,
        intention_id: str,
    ) -> Dict[str, Any]:
        """Mark an intention as completed."""
        now = datetime.now(timezone.utc).isoformat()
        self._update_status(intention_id, "completed", completed_at=now)
        return {"intention_id": intention_id, "status": "completed", "completed_at": now}

    def cancel_intention(
        self,
        intention_id: str,
    ) -> Dict[str, Any]:
        """Cancel a pending intention."""
        self._update_status(intention_id, "cancelled")
        return {"intention_id": intention_id, "status": "cancelled"}

    def get_due_intentions(self) -> List[Dict[str, Any]]:
        """Get all time-triggered intentions that are past due.

        Convenience method that only evaluates time triggers.
        """
        return self.check_triggers(events=None, context=None)

    def _update_status(
        self,
        memory_id: str,
        status: str,
        triggered_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        """Update intention status in memory metadata."""
        updates: Dict[str, Any] = {"pm_status": status}
        if triggered_at:
            updates["pm_triggered_at"] = triggered_at
        if completed_at:
            updates["pm_completed_at"] = completed_at

        try:
            self.memory.update(memory_id, {"metadata": updates})
        except Exception as e:
            logger.warning("Failed to update intention %s: %s", memory_id, e)

    @staticmethod
    def _format_intention(
        mem: Dict[str, Any],
        metadata: Dict[str, Any],
        status_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Format an intention memory into a clean dict."""
        return {
            "id": mem.get("id"),
            "description": metadata.get("pm_description", ""),
            "trigger_type": metadata.get("pm_trigger_type", ""),
            "trigger_value": metadata.get("pm_trigger_value", ""),
            "priority": int(metadata.get("pm_priority", 5)),
            "status": status_override or metadata.get("pm_status", "pending"),
            "action": metadata.get("pm_action", ""),
            "context": metadata.get("pm_context", ""),
            "agent_id": metadata.get("pm_agent_id", ""),
            "created_at": metadata.get("pm_created_at", ""),
            "triggered_at": metadata.get("pm_triggered_at"),
            "completed_at": metadata.get("pm_completed_at"),
            "expiry": metadata.get("pm_expiry"),
        }
