"""MCP tool definitions for engram-spawn."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register spawn MCP tools on the given server."""
    from engram_spawn.spawner import Spawner

    router = kwargs.get("router")
    llm = kwargs.get("llm")
    spawner = Spawner(memory, router=router, llm=llm)

    tool_defs = {
        "decompose_task": {
            "description": "Break a complex task into sub-tasks using LLM-based decomposition",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Parent task ID to decompose"},
                    "strategy": {"type": "string", "enum": ["auto", "sequential", "parallel", "phased"],
                                 "description": "Decomposition strategy", "default": "auto"},
                    "max_subtasks": {"type": "integer", "description": "Max sub-tasks", "default": 5},
                },
                "required": ["task_id"],
            },
        },
        "spawn_subtasks": {
            "description": "Create sub-tasks from decomposition results and optionally route them",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "parent_task_id": {"type": "string", "description": "Parent task ID"},
                    "subtasks": {"type": "array", "items": {"type": "object"}, "description": "Sub-task definitions"},
                },
                "required": ["parent_task_id", "subtasks"],
            },
        },
        "track_progress": {
            "description": "Check sub-task completion status for a parent task",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "parent_task_id": {"type": "string", "description": "Parent task ID"},
                },
                "required": ["parent_task_id"],
            },
        },
        "aggregate_results": {
            "description": "Collect results from completed sub-tasks",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "parent_task_id": {"type": "string", "description": "Parent task ID"},
                },
                "required": ["parent_task_id"],
            },
        },
        "cancel_subtasks": {
            "description": "Cancel all incomplete sub-tasks for a parent task",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "parent_task_id": {"type": "string", "description": "Parent task ID"},
                },
                "required": ["parent_task_id"],
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        if name == "decompose_task":
            return spawner.decompose(
                args["task_id"],
                strategy=args.get("strategy", "auto"),
                max_subtasks=args.get("max_subtasks", 5),
            )
        elif name == "spawn_subtasks":
            return spawner.spawn(args["parent_task_id"], args["subtasks"])
        elif name == "track_progress":
            return spawner.track(args["parent_task_id"])
        elif name == "aggregate_results":
            return spawner.aggregate(args["parent_task_id"])
        elif name == "cancel_subtasks":
            return {"cancelled": spawner.cancel(args["parent_task_id"])}
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_spawn_tools"):
        server._spawn_tools = {}
    server._spawn_tools.update(tool_defs)
    server._spawn_handler = _handle
