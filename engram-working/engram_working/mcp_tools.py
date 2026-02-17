"""MCP tool definitions for engram-working."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level working memory instances keyed by user_id
_instances: dict[str, Any] = {}


def _get_wm(memory: Any, user_id: str) -> Any:
    """Get or create a WorkingMemory instance for a user."""
    if user_id not in _instances:
        from engram_working.working import WorkingMemory
        _instances[user_id] = WorkingMemory(memory, user_id=user_id)
    return _instances[user_id]


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register working memory MCP tools on the given server."""

    tool_defs = {
        "wm_push": {
            "description": "Push an item into working memory (short-term volatile buffer)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to hold in working memory"},
                    "tag": {"type": "string", "description": "Tag for this item (e.g. 'task', 'context', 'goal')"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["content"],
            },
        },
        "wm_peek": {
            "description": "Look at a working memory item (refreshes its activation)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Working memory item key"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["key"],
            },
        },
        "wm_list": {
            "description": "List all items in working memory sorted by activation",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier"},
                },
            },
        },
        "wm_pop": {
            "description": "Remove an item from working memory",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Working memory item key"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["key"],
            },
        },
        "wm_flush_to_longterm": {
            "description": "Flush all working memory items to long-term memory and clear buffer",
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
        wm = _get_wm(memory, user_id)

        if name == "wm_push":
            return wm.push(
                content=args["content"],
                tag=args.get("tag", ""),
                metadata=args.get("metadata"),
            )
        elif name == "wm_peek":
            result = wm.peek(args["key"])
            return result or {"error": f"Item '{args['key']}' not found in working memory"}
        elif name == "wm_list":
            items = wm.list()
            return {"items": items, "count": len(items), "capacity": wm.config.capacity}
        elif name == "wm_pop":
            result = wm.pop(args["key"])
            return result or {"error": f"Item '{args['key']}' not found in working memory"}
        elif name == "wm_flush_to_longterm":
            return wm.flush_to_longterm()
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_working_tools"):
        server._working_tools = {}
    server._working_tools.update(tool_defs)
    server._working_handler = _handle
