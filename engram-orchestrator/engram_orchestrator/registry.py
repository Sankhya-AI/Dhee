"""AgentRegistry — stores agent capability profiles as memories.

Each agent is stored as a memory with memory_type="agent". Capability
matching uses Engram's semantic search over the agent description +
capabilities text.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Registry of agent capabilities backed by Engram Memory."""

    def __init__(self, memory: Any, user_id: str = "system") -> None:
        self._memory = memory
        self._user_id = user_id

    # ── Helpers ──

    def _build_content(self, agent_name: str, description: str, capabilities: list[str]) -> str:
        caps_text = ", ".join(capabilities)
        return f"{agent_name}: {description} Capabilities: {caps_text}"

    def _find_agent_memory(self, agent_name: str) -> dict | None:
        """Find existing agent memory by agent_name metadata."""
        results = self._memory.get_all(
            user_id=self._user_id,
            filters={"memory_type": "agent", "agent_name": agent_name},
            limit=1,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return items[0] if items else None

    def _format_agent(self, mem: dict) -> dict:
        """Format a raw memory into an agent info dict."""
        md = mem.get("metadata", {})
        return {
            "id": mem.get("id", ""),
            "name": md.get("agent_name", ""),
            "type": md.get("agent_type", ""),
            "model": md.get("agent_model", ""),
            "description": mem.get("memory", mem.get("content", "")),
            "capabilities": md.get("agent_capabilities", []),
            "max_concurrent": md.get("agent_max_concurrent", 1),
            "status": md.get("agent_status", "offline"),
            "active_tasks": md.get("agent_active_tasks", []),
            "registered_at": md.get("agent_registered_at", ""),
            "last_seen": md.get("agent_last_seen", ""),
        }

    # ── Public API ──

    def register(
        self,
        agent_name: str,
        *,
        capabilities: list[str],
        description: str,
        agent_type: str,
        model: str,
        max_concurrent: int = 1,
    ) -> dict:
        """Register or update an agent's capability profile."""
        now = datetime.now(timezone.utc).isoformat()
        content = self._build_content(agent_name, description, capabilities)
        metadata = {
            "memory_type": "agent",
            "agent_name": agent_name,
            "agent_type": agent_type,
            "agent_model": model,
            "agent_capabilities": capabilities,
            "agent_max_concurrent": max_concurrent,
            "agent_status": "available",
            "agent_active_tasks": [],
            "agent_registered_at": now,
            "agent_last_seen": now,
        }

        existing = self._find_agent_memory(agent_name)
        if existing:
            # Update existing registration
            self._memory.update(existing["id"], {
                "content": content,
                "metadata": {**existing.get("metadata", {}), **metadata,
                             "agent_registered_at": existing.get("metadata", {}).get("agent_registered_at", now)},
            })
            updated = self._memory.get(existing["id"])
            return self._format_agent(updated) if updated else self._format_agent(existing)

        # New registration
        result = self._memory.add(
            content,
            user_id=self._user_id,
            metadata=metadata,
            categories=["agents/available"],
            infer=False,
        )
        items = result.get("results", [])
        if items:
            return self._format_agent(items[0])
        return {"name": agent_name, "status": "available"}

    def get(self, agent_name: str) -> dict | None:
        """Get a single agent's info by name."""
        mem = self._find_agent_memory(agent_name)
        if mem:
            return self._format_agent(mem)
        return None

    def list(self, status: str | None = None) -> list[dict]:
        """List all registered agents, optionally filtered by status."""
        results = self._memory.get_all(
            user_id=self._user_id,
            filters={"memory_type": "agent"},
            limit=100,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        agents = [self._format_agent(m) for m in items]
        if status:
            agents = [a for a in agents if a["status"] == status]
        return agents

    def update_status(self, agent_name: str, status: str) -> None:
        """Update an agent's availability status."""
        mem = self._find_agent_memory(agent_name)
        if not mem:
            logger.warning("Agent '%s' not found in registry", agent_name)
            return
        md = dict(mem.get("metadata", {}))
        md["agent_status"] = status
        md["agent_last_seen"] = datetime.now(timezone.utc).isoformat()
        self._memory.update(mem["id"], {"metadata": md})

    def find_capable(self, query: str, limit: int = 5) -> list[dict]:
        """Semantic search over agent capabilities."""
        results = self._memory.search(
            query,
            user_id=self._user_id,
            filters={"memory_type": "agent"},
            limit=limit,
            use_echo_rerank=False,
        )
        items = results.get("results", [])
        agents = []
        for item in items:
            agent = self._format_agent(item)
            agent["similarity"] = item.get("score", item.get("similarity", 0.0))
            agents.append(agent)
        return agents

    def heartbeat(self, agent_name: str) -> None:
        """Update last_seen timestamp for an agent."""
        mem = self._find_agent_memory(agent_name)
        if not mem:
            return
        md = dict(mem.get("metadata", {}))
        md["agent_last_seen"] = datetime.now(timezone.utc).isoformat()
        self._memory.update(mem["id"], {"metadata": md})

    def add_active_task(self, agent_name: str, task_id: str) -> None:
        """Add a task to an agent's active task list."""
        mem = self._find_agent_memory(agent_name)
        if not mem:
            return
        md = dict(mem.get("metadata", {}))
        active = list(md.get("agent_active_tasks", []))
        if task_id not in active:
            active.append(task_id)
        md["agent_active_tasks"] = active
        md["agent_last_seen"] = datetime.now(timezone.utc).isoformat()
        # Auto-update status based on capacity
        if len(active) >= md.get("agent_max_concurrent", 1):
            md["agent_status"] = "busy"
        self._memory.update(mem["id"], {"metadata": md})

    def remove_active_task(self, agent_name: str, task_id: str) -> None:
        """Remove a task from an agent's active task list."""
        mem = self._find_agent_memory(agent_name)
        if not mem:
            return
        md = dict(mem.get("metadata", {}))
        active = [t for t in md.get("agent_active_tasks", []) if t != task_id]
        md["agent_active_tasks"] = active
        md["agent_last_seen"] = datetime.now(timezone.utc).isoformat()
        # If agent was busy and now has capacity, set available
        if md.get("agent_status") == "busy" and len(active) < md.get("agent_max_concurrent", 1):
            md["agent_status"] = "available"
        self._memory.update(mem["id"], {"metadata": md})
