"""Identity — CRUD for agent identities stored as memories."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class Identity:
    """Manages agent identity as an Engram memory.

    Each identity is stored with memory_type="identity" and discoverable
    via semantic search over role, capabilities, and goals.
    """

    def __init__(self, memory: Any, agent_id: str, user_id: str = "system") -> None:
        self._memory = memory
        self._agent_id = agent_id
        self._user_id = user_id

    # ── Helpers ──

    def _find_identity_memory(self, agent_id: str | None = None) -> dict | None:
        """Find existing identity memory by agent_id metadata."""
        aid = agent_id or self._agent_id
        results = self._memory.get_all(
            user_id=self._user_id,
            filters={"memory_type": "identity", "identity_agent_id": aid},
            limit=1,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return items[0] if items else None

    def _format_identity(self, mem: dict) -> dict:
        """Format a raw memory into an identity dict."""
        md = mem.get("metadata", {})
        return {
            "id": mem.get("id", ""),
            "agent_id": md.get("identity_agent_id", ""),
            "name": md.get("identity_name", ""),
            "role": md.get("identity_role", ""),
            "goals": md.get("identity_goals", []),
            "style": md.get("identity_style", ""),
            "constraints": md.get("identity_constraints", []),
            "capabilities": md.get("identity_capabilities", []),
            "created_at": md.get("identity_created_at", ""),
            "updated_at": md.get("identity_updated_at", ""),
        }

    def _build_content(self, name: str, role: str, goals: list[str],
                       capabilities: list[str] | None = None) -> str:
        """Build searchable content string for semantic matching."""
        parts = [f"{name}: {role}."]
        if goals:
            parts.append(f"Goals: {', '.join(goals)}.")
        if capabilities:
            parts.append(f"Capabilities: {', '.join(capabilities)}.")
        return " ".join(parts)

    # ── Public API ──

    def declare(
        self,
        *,
        name: str,
        role: str,
        goals: list[str],
        style: str = "",
        constraints: list[str] | None = None,
        capabilities: list[str] | None = None,
    ) -> dict:
        """Create or update this agent's identity in memory."""
        now = datetime.now(timezone.utc).isoformat()
        content = self._build_content(name, role, goals, capabilities)

        metadata = {
            "memory_type": "identity",
            "identity_agent_id": self._agent_id,
            "identity_name": name,
            "identity_role": role,
            "identity_goals": goals,
            "identity_style": style,
            "identity_constraints": constraints or [],
            "identity_capabilities": capabilities or [],
            "identity_updated_at": now,
        }

        existing = self._find_identity_memory()
        if existing:
            metadata["identity_created_at"] = existing.get("metadata", {}).get(
                "identity_created_at", now
            )
            self._memory.update(existing["id"], {
                "content": content,
                "metadata": {**existing.get("metadata", {}), **metadata},
            })
            updated = self._memory.get(existing["id"])
            return self._format_identity(updated) if updated else self._format_identity(existing)

        metadata["identity_created_at"] = now
        result = self._memory.add(
            content,
            user_id=self._user_id,
            metadata=metadata,
            categories=["identities"],
            infer=False,
        )
        items = result.get("results", [])
        if items:
            return self._format_identity(items[0])
        return {"agent_id": self._agent_id, "name": name, "role": role}

    def load(self, agent_id: str | None = None) -> dict | None:
        """Load an agent's identity from memory."""
        mem = self._find_identity_memory(agent_id)
        if mem:
            return self._format_identity(mem)
        return None

    def update(self, **fields: Any) -> dict:
        """Partial update of identity fields."""
        existing = self._find_identity_memory()
        if not existing:
            raise ValueError(f"No identity found for agent '{self._agent_id}'. Call declare() first.")

        md = dict(existing.get("metadata", {}))
        now = datetime.now(timezone.utc).isoformat()

        for key, value in fields.items():
            md_key = f"identity_{key}"
            if md_key in md:
                md[md_key] = value
        md["identity_updated_at"] = now

        # Rebuild content
        content = self._build_content(
            md.get("identity_name", ""),
            md.get("identity_role", ""),
            md.get("identity_goals", []),
            md.get("identity_capabilities"),
        )

        self._memory.update(existing["id"], {"content": content, "metadata": md})
        updated = self._memory.get(existing["id"])
        return self._format_identity(updated) if updated else self._format_identity(existing)

    def discover(self, query: str, limit: int = 5) -> list[dict]:
        """Find other agents by role/capability description."""
        results = self._memory.search(
            query,
            user_id=self._user_id,
            filters={"memory_type": "identity"},
            limit=limit,
            use_echo_rerank=False,
        )
        items = results.get("results", [])
        identities = []
        for item in items:
            identity = self._format_identity(item)
            identity["similarity"] = item.get("score", item.get("similarity", 0.0))
            identities.append(identity)
        return identities

    def get_context_injection(self) -> str:
        """Generate a system prompt fragment with identity context."""
        identity = self.load()
        if not identity:
            return ""

        lines = [f"You are {identity['name']}, a {identity['role']}."]
        if identity.get("goals"):
            lines.append(f"Your goals: {', '.join(identity['goals'])}.")
        if identity.get("style"):
            lines.append(f"Communication style: {identity['style']}.")
        if identity.get("constraints"):
            lines.append(f"Constraints: {', '.join(identity['constraints'])}.")
        if identity.get("capabilities"):
            lines.append(f"Capabilities: {', '.join(identity['capabilities'])}.")
        return "\n".join(lines)

    def who_am_i(self) -> str:
        """Short identity summary for the agent itself."""
        identity = self.load()
        if not identity:
            return f"No identity declared for agent '{self._agent_id}'."
        return f"{identity['name']} — {identity['role']}"
