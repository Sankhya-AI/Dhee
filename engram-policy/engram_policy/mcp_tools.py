"""MCP tool definitions for engram-policy."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register policy MCP tools on the given server."""
    from engram_policy.engine import PolicyEngine
    from engram_policy.tokens import CapabilityToken

    engine = PolicyEngine(memory)
    tokens = CapabilityToken(memory)

    tool_defs = {
        "add_policy": {
            "description": "Add an access control rule for an agent",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent this policy applies to (or '*' for all)"},
                    "resource": {"type": "string", "description": "Resource pattern (supports wildcards)"},
                    "actions": {"type": "array", "items": {"type": "string"}, "description": "Allowed/denied actions"},
                    "effect": {"type": "string", "enum": ["allow", "deny"], "description": "Allow or deny", "default": "allow"},
                    "priority": {"type": "integer", "description": "Priority (higher = evaluated first)", "default": 0},
                    "conditions": {"type": "object", "description": "Additional conditions"},
                },
                "required": ["agent_id", "resource", "actions"],
            },
        },
        "check_access": {
            "description": "Check if an agent is allowed to perform an action on a resource",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent requesting access"},
                    "resource": {"type": "string", "description": "Resource being accessed"},
                    "action": {"type": "string", "description": "Action being attempted"},
                },
                "required": ["agent_id", "resource", "action"],
            },
        },
        "list_policies": {
            "description": "List active access control policies",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Filter by agent (optional)"},
                },
            },
        },
        "create_token": {
            "description": "Create a short-lived capability token for cross-agent operations",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent the token is for"},
                    "scopes": {"type": "array", "items": {"type": "string"}, "description": "Granted scopes"},
                    "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Granted capabilities"},
                    "ttl_minutes": {"type": "integer", "description": "Token lifetime in minutes", "default": 60},
                },
                "required": ["agent_id", "scopes", "capabilities"],
            },
        },
        "get_permissions": {
            "description": "Get effective permissions for an agent",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent to check"},
                },
                "required": ["agent_id"],
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        if name == "add_policy":
            return engine.add_policy(
                agent_id=args["agent_id"],
                resource=args["resource"],
                actions=args["actions"],
                effect=args.get("effect", "allow"),
                conditions=args.get("conditions"),
                priority=args.get("priority", 0),
            )
        elif name == "check_access":
            decision = engine.check_access(args["agent_id"], args["resource"], args["action"])
            return {"allowed": decision.allowed, "reason": decision.reason,
                    "matched_policy_id": decision.matched_policy_id, "layer": decision.layer}
        elif name == "list_policies":
            return engine.list_policies(agent_id=args.get("agent_id"))
        elif name == "create_token":
            token = tokens.create(
                agent_id=args["agent_id"],
                scopes=args["scopes"],
                capabilities=args["capabilities"],
                ttl_minutes=args.get("ttl_minutes", 60),
            )
            return {"token": token}
        elif name == "get_permissions":
            return engine.get_effective_permissions(args["agent_id"])
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_policy_tools"):
        server._policy_tools = {}
    server._policy_tools.update(tool_defs)
    server._policy_handler = _handle
