"""Auto-failover on rate limit — find replacement agent and hand off."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AutoFailover:
    """Auto-failover when an agent is rate-limited."""

    def __init__(self, bus: Any, registry: Any, config: Any = None) -> None:
        self._bus = bus
        self._registry = registry  # AgentRegistry from engram-router
        self._config = config

    def find_failover_agent(
        self,
        exclude: str | list[str] = "",
        task_query: str = "",
    ) -> str | None:
        """Find next available agent (not excluded, has capacity).

        Uses registry.list() + optional semantic match via find_capable().
        """
        if isinstance(exclude, str):
            exclude_set = {exclude} if exclude else set()
        else:
            exclude_set = set(exclude)

        # If we have a task query, try semantic match first
        if task_query:
            try:
                candidates = self._registry.find_capable(task_query, limit=5)
                for c in candidates:
                    name = c.get("name", c.get("agent_name", ""))
                    if name and name not in exclude_set:
                        status = c.get("status", "available")
                        if status in ("available", "idle"):
                            return name
            except Exception as e:
                logger.debug("Semantic failover search failed: %s", e)

        # Fallback: pick first available agent from registry
        try:
            all_agents = self._registry.list()
            for agent in all_agents:
                name = agent.get("name", agent.get("agent_name", ""))
                if name and name not in exclude_set:
                    status = agent.get("status", "available")
                    if status in ("available", "idle"):
                        return name
        except Exception as e:
            logger.warning("Failed to list agents for failover: %s", e)

        return None

    def execute_failover(
        self,
        from_agent: str,
        to_agent: str,
        user_id: str,
        task_id: str = "",
        last_message: str = "",
    ) -> dict[str, Any]:
        """Save session, open handoff lane, transfer bus keys, publish event.

        Returns {to_agent, session_id, lane_id}.
        """
        # Save the outgoing agent's session
        session = self._bus.save_session(
            agent_id=from_agent,
            task_summary=f"Rate limited. Failing over to {to_agent}. Last: {last_message[:200]}",
            repo="",
            status="paused",
        )
        session_id = session.get("id", "") if session else ""

        # Open handoff lane
        lane_id = ""
        if session_id:
            try:
                lane = self._bus.open_lane(
                    session_id=session_id,
                    from_agent=from_agent,
                    to_agent=to_agent,
                    context={
                        "reason": "rate_limited",
                        "task_id": task_id,
                        "last_message": last_message[:500],
                    },
                )
                lane_id = lane.get("id", "") if lane else ""
            except Exception as e:
                logger.warning("Failed to open handoff lane: %s", e)

        # Transfer active bus keys from the old agent's namespace
        try:
            keys = self._bus.keys(namespace="session", agent=from_agent)
            if keys:
                self._bus.transfer(from_agent, to_agent, keys, namespace="session")
        except Exception as e:
            logger.debug("Bus key transfer during failover: %s", e)

        result = {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "user_id": user_id,
            "task_id": task_id,
            "session_id": session_id,
            "lane_id": lane_id,
        }

        if self._bus:
            self._bus.publish("warroom.failover", result)

        logger.info("Failover: %s -> %s (user=%s, task=%s)", from_agent, to_agent, user_id, task_id)
        return result
