"""MCP tool definitions for engram-router."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register router MCP tools on the given server."""
    from engram_router.registry import AgentRegistry
    from engram_router.router import TaskRouter
    from engram_router.config import RouterConfig
    from engram.memory.tasks import TaskManager

    config = RouterConfig()
    registry = AgentRegistry(memory, user_id=config.user_id)
    tm = TaskManager(memory)
    router = TaskRouter(registry, tm, config=config, memory=memory)

    bus = kwargs.get("bus")
    if bus:
        router.connect_bus(bus)

    tool_defs = {
        "register_agent": {
            "description": "Register an agent's capabilities in memory for task routing",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "description": "Unique agent identifier"},
                    "capabilities": {"type": "array", "items": {"type": "string"}, "description": "List of capabilities"},
                    "description": {"type": "string", "description": "What this agent does"},
                    "agent_type": {"type": "string", "description": "Agent type (e.g. claude, codex, custom)"},
                    "model": {"type": "string", "description": "Model name/ID"},
                    "max_concurrent": {"type": "integer", "description": "Max concurrent tasks", "default": 1},
                },
                "required": ["agent_name", "capabilities", "description", "agent_type", "model"],
            },
        },
        "find_capable_agents": {
            "description": "Semantic search over agent capabilities to find suitable agents for a task",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Describe the task or capability needed"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
        },
        "route_task": {
            "description": "Auto-route a task to the best available agent based on capability matching",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID to route"},
                    "force": {"type": "boolean", "description": "Force re-route even if already assigned", "default": False},
                },
                "required": ["task_id"],
            },
        },
        "claim_task": {
            "description": "Atomic CAS claim on a task — agent takes ownership",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID to claim"},
                    "agent_name": {"type": "string", "description": "Agent claiming the task"},
                },
                "required": ["task_id", "agent_name"],
            },
        },
        "release_task": {
            "description": "Release a task back to the pool for reassignment",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID to release"},
                    "agent_name": {"type": "string", "description": "Agent releasing the task"},
                },
                "required": ["task_id", "agent_name"],
            },
        },
        "list_agents": {
            "description": "List all registered agents with their status and capabilities",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter by status (available, busy, offline)"},
                },
            },
        },
    }

    _handlers = {
        "register_agent": lambda args: registry.register(
            args["agent_name"],
            capabilities=args["capabilities"],
            description=args["description"],
            agent_type=args["agent_type"],
            model=args["model"],
            max_concurrent=args.get("max_concurrent", 1),
        ),
        "find_capable_agents": lambda args: registry.find_capable(
            args["query"], limit=args.get("limit", 5),
        ),
        "route_task": lambda args: router.route(
            args["task_id"], force=args.get("force", False),
        ),
        "claim_task": lambda args: router.claim(
            args["task_id"], args["agent_name"],
        ),
        "release_task": lambda args: router.release(
            args["task_id"], args["agent_name"],
        ),
        "list_agents": lambda args: registry.list(
            status=args.get("status"),
        ),
    }

    # Register tools and handlers with the server
    _register_with_server(server, tool_defs, _handlers)


def _register_with_server(server: Any, tool_defs: dict, handlers: dict) -> None:
    """Register tool definitions and handlers with the MCP server."""
    # Store for the call_tool handler
    if not hasattr(server, "_router_tools"):
        server._router_tools = {}
        server._router_handlers = {}
    server._router_tools.update(tool_defs)
    server._router_handlers.update(handlers)
