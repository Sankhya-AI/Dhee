"""MCP tool definitions for engram-heartbeat."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register heartbeat MCP tools on the given server."""
    from engram_heartbeat.heartbeat import Heartbeat

    # Cache heartbeat instances per agent_id
    _instances: dict[str, Heartbeat] = {}

    def _get_hb(agent_id: str) -> Heartbeat:
        if agent_id not in _instances:
            bus = kwargs.get("bus")
            _instances[agent_id] = Heartbeat(memory, agent_id, bus=bus)
        return _instances[agent_id]

    tool_defs = {
        "schedule_heartbeat": {
            "description": "Register a recurring behavior (decay, consolidation, health check, etc.)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                    "name": {"type": "string", "description": "Heartbeat name"},
                    "action": {"type": "string", "description": "Behavior to run (decay, consolidation, health_check, stale_task_check, memory_stats)"},
                    "interval_minutes": {"type": "integer", "description": "Run every N minutes"},
                    "params": {"type": "object", "description": "Action parameters"},
                    "enabled": {"type": "boolean", "description": "Start enabled", "default": True},
                },
                "required": ["agent_id", "name", "action", "interval_minutes"],
            },
        },
        "list_heartbeats": {
            "description": "List all scheduled behaviors for an agent",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                },
                "required": ["agent_id"],
            },
        },
        "remove_heartbeat": {
            "description": "Remove a scheduled behavior",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                    "heartbeat_id": {"type": "string", "description": "Heartbeat memory ID"},
                },
                "required": ["agent_id", "heartbeat_id"],
            },
        },
        "tick_heartbeat": {
            "description": "Manually trigger all due behaviors now",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                },
                "required": ["agent_id"],
            },
        },
        "heartbeat_status": {
            "description": "Show runner status and next-due times for an agent's heartbeats",
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
        hb = _get_hb(agent_id)

        if name == "schedule_heartbeat":
            return hb.schedule(
                name=args["name"],
                action=args["action"],
                interval_minutes=args["interval_minutes"],
                params=args.get("params"),
                enabled=args.get("enabled", True),
            )
        elif name == "list_heartbeats":
            return hb.list()
        elif name == "remove_heartbeat":
            return {"removed": hb.remove(args["heartbeat_id"])}
        elif name == "tick_heartbeat":
            return {"results": hb.tick()}
        elif name == "heartbeat_status":
            heartbeats = hb.list()
            return {
                "agent_id": agent_id,
                "runner_active": hb._runner.is_running,
                "heartbeat_count": len(heartbeats),
                "heartbeats": heartbeats,
            }
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_heartbeat_tools"):
        server._heartbeat_tools = {}
    server._heartbeat_tools.update(tool_defs)
    server._heartbeat_handler = _handle
