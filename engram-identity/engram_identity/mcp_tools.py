"""MCP tool definitions for engram-identity."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register identity MCP tools on the given server."""
    from engram_identity.identity import Identity

    tool_defs = {
        "declare_identity": {
            "description": "Set this agent's persona, role, goals, and communication style",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                    "name": {"type": "string", "description": "Display name"},
                    "role": {"type": "string", "description": "Role description"},
                    "goals": {"type": "array", "items": {"type": "string"}, "description": "List of goals"},
                    "style": {"type": "string", "description": "Communication style"},
                    "constraints": {"type": "array", "items": {"type": "string"}, "description": "Behavioral constraints"},
                    "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Skills/capabilities"},
                },
                "required": ["agent_id", "name", "role", "goals"],
            },
        },
        "load_identity": {
            "description": "Load an agent's identity from memory",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                },
                "required": ["agent_id"],
            },
        },
        "discover_agents": {
            "description": "Semantic search over agent identities to find agents by role or capability",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Describe the role or capability to find"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
        },
        "who_am_i": {
            "description": "Get this agent's identity summary",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                },
                "required": ["agent_id"],
            },
        },
        "get_identity_context": {
            "description": "Get a system prompt injection fragment with identity context",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                },
                "required": ["agent_id"],
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        agent_id = args.get("agent_id", "default")
        identity = Identity(memory, agent_id)

        if name == "declare_identity":
            return identity.declare(
                name=args["name"],
                role=args["role"],
                goals=args["goals"],
                style=args.get("style", ""),
                constraints=args.get("constraints"),
                capabilities=args.get("capabilities"),
            )
        elif name == "load_identity":
            result = identity.load()
            return result or {"error": f"No identity found for agent '{agent_id}'"}
        elif name == "discover_agents":
            return identity.discover(args["query"], limit=args.get("limit", 5))
        elif name == "who_am_i":
            return {"summary": identity.who_am_i()}
        elif name == "get_identity_context":
            return {"context": identity.get_context_injection()}
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_identity_tools"):
        server._identity_tools = {}
        server._identity_handlers = {}
    server._identity_tools.update(tool_defs)
    server._identity_handlers = _handle
