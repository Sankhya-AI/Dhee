"""MCP tool definitions for engram-prospective."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register prospective memory MCP tools on the given server."""
    from engram_prospective.prospective import Prospective

    tool_defs = {
        "add_intention": {
            "description": "Remember something to do later with a time, event, or condition trigger.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What needs to be done"},
                    "trigger_type": {"type": "string", "description": "One of: time, event, condition"},
                    "trigger_value": {"type": "string", "description": "Trigger spec: ISO datetime for time, event name for event, key=value for condition"},
                    "action": {"type": "string", "description": "What to do when triggered"},
                    "priority": {"type": "integer", "description": "1 (highest) to 10 (lowest), default 5"},
                    "agent_id": {"type": "string", "description": "Agent that owns this intention"},
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                    "context": {"type": "string", "description": "Additional context"},
                    "expiry": {"type": "string", "description": "ISO datetime when intention expires"},
                },
                "required": ["description", "trigger_type", "trigger_value"],
            },
        },
        "list_intentions": {
            "description": "List intentions filtered by status (pending, triggered, completed, expired, cancelled).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                    "status": {"type": "string", "description": "Filter by status", "default": "pending"},
                    "limit": {"type": "integer", "description": "Max results", "default": 20},
                },
            },
        },
        "check_intention_triggers": {
            "description": "Evaluate all pending intentions and return those whose triggers have fired.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                    "events": {"type": "object", "description": "Dict of recent events for event-trigger matching"},
                    "context": {"type": "object", "description": "Dict of current context for condition-trigger matching"},
                },
            },
        },
        "complete_intention": {
            "description": "Mark an intention as completed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "intention_id": {"type": "string", "description": "ID of the intention to complete"},
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                },
                "required": ["intention_id"],
            },
        },
        "cancel_intention": {
            "description": "Cancel a pending intention.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "intention_id": {"type": "string", "description": "ID of the intention to cancel"},
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                },
                "required": ["intention_id"],
            },
        },
        "get_due_intentions": {
            "description": "Get all time-triggered intentions that are past due.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                },
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        user_id = args.get("user_id", "default")
        pm = Prospective(memory, user_id=user_id)

        if name == "add_intention":
            return pm.add_intention(
                description=args["description"],
                trigger_type=args["trigger_type"],
                trigger_value=args["trigger_value"],
                action=args.get("action"),
                priority=args.get("priority"),
                agent_id=args.get("agent_id"),
                context=args.get("context"),
                expiry=args.get("expiry"),
            )
        elif name == "list_intentions":
            return pm.list_intentions(
                status=args.get("status", "pending"),
                limit=args.get("limit", 20),
            )
        elif name == "check_intention_triggers":
            return pm.check_triggers(
                events=args.get("events"),
                context=args.get("context"),
            )
        elif name == "complete_intention":
            return pm.complete_intention(intention_id=args["intention_id"])
        elif name == "cancel_intention":
            return pm.cancel_intention(intention_id=args["intention_id"])
        elif name == "get_due_intentions":
            return pm.get_due_intentions()
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_prospective_tools"):
        server._prospective_tools = {}
        server._prospective_handler = {}
    server._prospective_tools.update(tool_defs)
    server._prospective_handler = _handle
