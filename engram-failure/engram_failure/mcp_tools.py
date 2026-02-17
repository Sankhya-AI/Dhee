"""MCP tool definitions for engram-failure."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register failure learning MCP tools on the given server."""
    from engram_failure.failure import FailureLearning

    tool_defs = {
        "log_failure": {
            "description": "Log a failure with action, error, and context for future learning",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "What action was attempted"},
                    "error": {"type": "string", "description": "What went wrong"},
                    "context": {"type": "string", "description": "Surrounding context"},
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "description": "Failure severity",
                        "default": "medium",
                    },
                    "agent_id": {"type": "string", "description": "Agent that experienced the failure"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["action", "error"],
            },
        },
        "search_failures": {
            "description": "Search past failures for similar situations",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Describe the failure situation"},
                    "limit": {"type": "integer", "description": "Max results", "default": 10},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["query"],
            },
        },
        "extract_antipattern": {
            "description": "Extract an anti-pattern (what NOT to do) from failure episodes",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "failure_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs of failure memories to analyze",
                    },
                    "name": {"type": "string", "description": "Name for the anti-pattern"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["failure_ids"],
            },
        },
        "list_antipatterns": {
            "description": "List extracted anti-patterns (things to avoid)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max results", "default": 20},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
            },
        },
        "search_recovery_strategies": {
            "description": "Search for recovery strategies from past resolved failures",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Describe what went wrong"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["query"],
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        user_id = args.get("user_id", "default")
        fl = FailureLearning(memory, user_id=user_id)

        if name == "log_failure":
            return fl.log_failure(
                action=args["action"],
                error=args["error"],
                context=args.get("context", ""),
                severity=args.get("severity", "medium"),
                agent_id=args.get("agent_id", ""),
            )
        elif name == "search_failures":
            return fl.search_failures(args["query"], limit=args.get("limit", 10))
        elif name == "extract_antipattern":
            return fl.extract_antipattern(
                failure_ids=args["failure_ids"],
                name=args.get("name", ""),
            )
        elif name == "list_antipatterns":
            return fl.list_antipatterns(limit=args.get("limit", 20))
        elif name == "search_recovery_strategies":
            return fl.search_recovery_strategies(
                query=args["query"], limit=args.get("limit", 5)
            )
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_failure_tools"):
        server._failure_tools = {}
    server._failure_tools.update(tool_defs)
    server._failure_handler = _handle
