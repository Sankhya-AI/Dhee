"""MCP tool definitions for engram-reconsolidation."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register reconsolidation MCP tools on the given server."""
    from engram_reconsolidation.reconsolidation import Reconsolidation

    tool_defs = {
        "propose_memory_update": {
            "description": "Propose an update to a memory based on new context",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "ID of memory to update"},
                    "new_context": {"type": "string", "description": "New context that may refine the memory"},
                    "agent_id": {"type": "string", "description": "Agent proposing the update"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["memory_id", "new_context"],
            },
        },
        "apply_memory_update": {
            "description": "Apply a pending reconsolidation proposal",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string", "description": "Proposal memory ID"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["proposal_id"],
            },
        },
        "reject_memory_update": {
            "description": "Reject a pending reconsolidation proposal",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string", "description": "Proposal memory ID"},
                    "reason": {"type": "string", "description": "Reason for rejection"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["proposal_id"],
            },
        },
        "get_memory_versions": {
            "description": "Get full version/edit history of a memory",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "Memory ID to get history for"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["memory_id"],
            },
        },
        "list_pending_updates": {
            "description": "List reconsolidation proposals awaiting approval",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max results", "default": 20},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
            },
        },
        "get_reconsolidation_stats": {
            "description": "Get statistics on reconsolidation activity",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier"},
                },
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        user_id = args.get("user_id", "default")
        rc = Reconsolidation(memory, user_id=user_id)

        if name == "propose_memory_update":
            return rc.propose_update(
                memory_id=args["memory_id"],
                new_context=args["new_context"],
                agent_id=args.get("agent_id", ""),
            )
        elif name == "apply_memory_update":
            return rc.apply_update(proposal_id=args["proposal_id"])
        elif name == "reject_memory_update":
            return rc.reject_update(
                proposal_id=args["proposal_id"],
                reason=args.get("reason", ""),
            )
        elif name == "get_memory_versions":
            return rc.get_version_history(memory_id=args["memory_id"])
        elif name == "list_pending_updates":
            return rc.list_pending_proposals(limit=args.get("limit", 20))
        elif name == "get_reconsolidation_stats":
            return rc.get_stats()
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_reconsolidation_tools"):
        server._reconsolidation_tools = {}
    server._reconsolidation_tools.update(tool_defs)
    server._reconsolidation_handler = _handle
