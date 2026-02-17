"""MCP tool definitions for engram-skills."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register skills MCP tools on the given server."""
    from engram_skills.registry import SkillRegistry

    registry = SkillRegistry(memory)

    tool_defs = {
        "register_skill": {
            "description": "Register a shareable skill/tool for discovery by other agents",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "description": {"type": "string", "description": "What this skill does"},
                    "parameters": {"type": "object", "description": "Parameter name->type map"},
                    "examples": {"type": "array", "items": {"type": "string"}, "description": "Usage examples"},
                    "agent_id": {"type": "string", "description": "Owning agent"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Skill tags"},
                },
                "required": ["name", "description"],
            },
        },
        "search_skills": {
            "description": "Find skills by description using semantic search",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Describe the skill/capability needed"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
        },
        "list_skills": {
            "description": "List all registered skills",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Filter by owning agent"},
                },
            },
        },
        "get_skill": {
            "description": "Get skill details by name",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                },
                "required": ["name"],
            },
        },
        "invoke_skill": {
            "description": "Invoke a locally-registered skill",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "params": {"type": "object", "description": "Skill parameters"},
                },
                "required": ["name"],
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        if name == "register_skill":
            return registry.register(
                name=args["name"],
                description=args["description"],
                parameters=args.get("parameters"),
                examples=args.get("examples"),
                agent_id=args.get("agent_id"),
                tags=args.get("tags"),
            )
        elif name == "search_skills":
            return registry.search(args["query"], limit=args.get("limit", 5))
        elif name == "list_skills":
            return registry.list(agent_id=args.get("agent_id"))
        elif name == "get_skill":
            result = registry.get(args["name"])
            return result or {"error": f"Skill '{args['name']}' not found"}
        elif name == "invoke_skill":
            try:
                result = registry.invoke(args["name"], **args.get("params", {}))
                return {"result": result}
            except Exception as e:
                return {"error": str(e)}
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_skills_tools"):
        server._skills_tools = {}
    server._skills_tools.update(tool_defs)
    server._skills_handler = _handle
