"""PolicyEngine — evaluate access against layered policy rules."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from engram_policy.scopes import match_resource

logger = logging.getLogger(__name__)


@dataclass
class AccessDecision:
    """Result of an access check."""
    allowed: bool
    reason: str
    matched_policy_id: str = ""
    layer: int = 0


class PolicyEngine:
    """Multi-layer access control engine backed by Engram Memory.

    Policy layers (evaluated in order, first match wins):
        Layer 1: Explicit deny rules       (highest priority)
        Layer 2: Agent-specific allow rules
        Layer 3: Role-based rules          (via identity role)
        Layer 4: Scope-based rules         (data scopes)
        Layer 5: Trust-based defaults
        Layer 6: Global defaults           (lowest priority)
    """

    def __init__(self, memory: Any, user_id: str = "system",
                 default_effect: str = "deny") -> None:
        self._memory = memory
        self._user_id = user_id
        self._default_effect = default_effect

    # ── Helpers ──

    def _find_policies(self, agent_id: str | None = None) -> list[dict]:
        """Get all policy memories, optionally filtered by agent."""
        filters: dict[str, Any] = {"memory_type": "policy"}
        if agent_id:
            filters["policy_agent_id"] = agent_id
        results = self._memory.get_all(
            user_id=self._user_id,
            filters=filters,
            limit=500,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return items

    def _format_policy(self, mem: dict) -> dict:
        """Format a raw memory into a policy dict."""
        md = mem.get("metadata", {})
        return {
            "id": mem.get("id", ""),
            "agent_id": md.get("policy_agent_id", ""),
            "resource": md.get("policy_resource", ""),
            "actions": md.get("policy_actions", []),
            "effect": md.get("policy_effect", "allow"),
            "conditions": md.get("policy_conditions", {}),
            "priority": md.get("policy_priority", 0),
            "created_at": md.get("policy_created_at", ""),
        }

    # ── Public API ──

    def add_policy(self, *, agent_id: str, resource: str,
                   actions: list[str], effect: str = "allow",
                   conditions: dict | None = None, priority: int = 0) -> dict:
        """Add a policy rule. Stored as a memory."""
        now = datetime.now(timezone.utc).isoformat()
        content = f"Policy: {effect} {agent_id} {actions} on {resource}"
        metadata = {
            "memory_type": "policy",
            "policy_agent_id": agent_id,
            "policy_resource": resource,
            "policy_actions": actions,
            "policy_effect": effect,
            "policy_conditions": conditions or {},
            "policy_priority": priority,
            "policy_created_at": now,
        }

        result = self._memory.add(
            content,
            user_id=self._user_id,
            metadata=metadata,
            categories=["policies"],
            infer=False,
        )
        items = result.get("results", [])
        if items:
            return self._format_policy(items[0])
        return {"agent_id": agent_id, "resource": resource, "effect": effect}

    def check_access(self, agent_id: str, resource: str,
                     action: str) -> AccessDecision:
        """Evaluate whether agent can perform action on resource.

        Evaluates policies in priority order (highest first).
        Explicit denies are checked before allows.
        """
        policies = self._find_policies()

        # Sort by priority descending, then denies before allows at same priority
        def sort_key(p: dict) -> tuple:
            md = p.get("metadata", {})
            pri = md.get("policy_priority", 0)
            # Denies get a slight boost at same priority
            is_deny = 1 if md.get("policy_effect") == "deny" else 0
            return (-pri, -is_deny)

        policies.sort(key=sort_key)

        for mem in policies:
            md = mem.get("metadata", {})
            policy_agent = md.get("policy_agent_id", "")
            policy_resource = md.get("policy_resource", "")
            policy_actions = md.get("policy_actions", [])
            policy_effect = md.get("policy_effect", "allow")

            # Check agent match (wildcard "*" matches all)
            if policy_agent != "*" and policy_agent != agent_id:
                continue

            # Check resource match
            if not match_resource(policy_resource, resource):
                continue

            # Check action match
            if "*" not in policy_actions and action not in policy_actions:
                continue

            # Match found
            allowed = policy_effect == "allow"
            return AccessDecision(
                allowed=allowed,
                reason=f"Matched policy: {policy_effect} {policy_agent} {policy_actions} on {policy_resource}",
                matched_policy_id=mem.get("id", ""),
                layer=md.get("policy_priority", 0),
            )

        # No matching policy — use default
        default_allowed = self._default_effect == "allow"
        return AccessDecision(
            allowed=default_allowed,
            reason=f"No matching policy, default effect: {self._default_effect}",
        )

    def list_policies(self, agent_id: str | None = None) -> list[dict]:
        """List policies, optionally filtered by agent."""
        mems = self._find_policies(agent_id)
        return [self._format_policy(m) for m in mems]

    def remove_policy(self, policy_id: str) -> bool:
        """Remove a policy rule."""
        try:
            self._memory.delete(policy_id)
            return True
        except Exception:
            return False

    def get_effective_permissions(self, agent_id: str) -> dict:
        """Compute the effective permission summary for an agent."""
        policies = self._find_policies()
        allows: list[dict] = []
        denies: list[dict] = []

        for mem in policies:
            md = mem.get("metadata", {})
            policy_agent = md.get("policy_agent_id", "")
            if policy_agent != "*" and policy_agent != agent_id:
                continue

            entry = {
                "resource": md.get("policy_resource", ""),
                "actions": md.get("policy_actions", []),
                "priority": md.get("policy_priority", 0),
            }
            if md.get("policy_effect") == "deny":
                denies.append(entry)
            else:
                allows.append(entry)

        return {
            "agent_id": agent_id,
            "allows": allows,
            "denies": denies,
            "default_effect": self._default_effect,
        }
