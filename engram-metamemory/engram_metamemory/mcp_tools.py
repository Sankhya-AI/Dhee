"""MCP tool definitions for engram-metamemory."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register metamemory MCP tools on the given server."""
    from engram_metamemory.metamemory import Metamemory

    tool_defs = {
        "feeling_of_knowing": {
            "description": "Assess whether the system knows about a topic. Returns confident/uncertain/unknown verdict with confidence score.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Topic or question to assess knowledge of"},
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                    "limit": {"type": "integer", "description": "Max supporting memories to check", "default": 5},
                },
                "required": ["query"],
            },
        },
        "list_knowledge_gaps": {
            "description": "List things the system knows it doesn't know. Returns tracked knowledge gaps.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                    "status": {"type": "string", "description": "Filter by status: open or resolved", "default": "open"},
                    "limit": {"type": "integer", "description": "Max gaps to return", "default": 20},
                },
            },
        },
        "resolve_knowledge_gap": {
            "description": "Mark a knowledge gap as resolved — the system now knows about this topic.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "gap_id": {"type": "string", "description": "ID of the gap to resolve"},
                },
                "required": ["gap_id"],
            },
        },
        "log_retrieval_outcome": {
            "description": "Record whether a retrieval was useful, wrong, irrelevant, or partial. Used for calibration.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query that was used"},
                    "memory_ids": {"type": "array", "items": {"type": "string"}, "description": "IDs of retrieved memories"},
                    "outcome": {"type": "string", "description": "One of: useful, wrong, irrelevant, partial"},
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                    "correction": {"type": "string", "description": "Optional correction if outcome was wrong"},
                },
                "required": ["query", "memory_ids", "outcome"],
            },
        },
        "get_calibration_stats": {
            "description": "Get accuracy statistics — how well confidence predicts retrieval usefulness.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                },
            },
        },
        "get_memory_confidence": {
            "description": "Get detailed confidence breakdown for a specific memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "ID of the memory to inspect"},
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                },
                "required": ["memory_id"],
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        user_id = args.get("user_id", "default")
        mm = Metamemory(memory, user_id=user_id)

        if name == "feeling_of_knowing":
            return mm.feeling_of_knowing(
                query=args["query"],
                limit=args.get("limit", 5),
            )
        elif name == "list_knowledge_gaps":
            return mm.list_knowledge_gaps(
                status=args.get("status", "open"),
                limit=args.get("limit", 20),
            )
        elif name == "resolve_knowledge_gap":
            return mm.resolve_knowledge_gap(gap_id=args["gap_id"])
        elif name == "log_retrieval_outcome":
            return mm.log_retrieval_outcome(
                query=args["query"],
                memory_ids=args["memory_ids"],
                outcome=args["outcome"],
                correction=args.get("correction"),
            )
        elif name == "get_calibration_stats":
            return mm.get_calibration_stats()
        elif name == "get_memory_confidence":
            return mm.get_memory_confidence(memory_id=args["memory_id"])
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_metamemory_tools"):
        server._metamemory_tools = {}
        server._metamemory_handler = {}
    server._metamemory_tools.update(tool_defs)
    server._metamemory_handler = _handle
