"""MCP tool definitions for engram-procedural."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register procedural MCP tools on the given server."""
    from engram_procedural.procedural import Procedural

    tool_defs = {
        "extract_procedure": {
            "description": "Extract a reusable step-by-step procedure from episode memories",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "episode_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs of episode memories to extract procedure from",
                    },
                    "name": {"type": "string", "description": "Name for the procedure"},
                    "domain": {"type": "string", "description": "Domain (e.g. 'python', 'devops')"},
                    "agent_id": {"type": "string", "description": "Agent extracting the procedure"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["episode_ids", "name"],
            },
        },
        "get_procedure": {
            "description": "Get a procedure's steps, stats, and execution history",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Procedure name"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["name"],
            },
        },
        "search_procedures": {
            "description": "Semantic search for relevant procedures",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results", "default": 10},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["query"],
            },
        },
        "log_procedure_execution": {
            "description": "Record success/failure of a procedure execution",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "Procedure memory ID"},
                    "success": {"type": "boolean", "description": "Whether the execution succeeded"},
                    "context": {"type": "string", "description": "Execution context"},
                    "notes": {"type": "string", "description": "Additional notes"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["procedure_id", "success"],
            },
        },
        "refine_procedure": {
            "description": "Update procedure steps based on new experience",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "Procedure memory ID"},
                    "correction": {"type": "string", "description": "Correction or refinement to apply"},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
                "required": ["procedure_id", "correction"],
            },
        },
        "list_procedures": {
            "description": "List procedures by status (active/deprecated/draft)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["active", "deprecated", "draft"],
                        "description": "Filter by status",
                        "default": "active",
                    },
                    "limit": {"type": "integer", "description": "Max results", "default": 20},
                    "user_id": {"type": "string", "description": "User identifier"},
                },
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        user_id = args.get("user_id", "default")
        proc = Procedural(memory, user_id=user_id)

        if name == "extract_procedure":
            return proc.extract_procedure(
                episode_ids=args["episode_ids"],
                name=args["name"],
                domain=args.get("domain", ""),
                agent_id=args.get("agent_id", ""),
            )
        elif name == "get_procedure":
            result = proc.get_procedure(args["name"])
            return result or {"error": f"Procedure '{args['name']}' not found"}
        elif name == "search_procedures":
            return proc.search_procedures(
                query=args["query"], limit=args.get("limit", 10)
            )
        elif name == "log_procedure_execution":
            return proc.log_execution(
                procedure_id=args["procedure_id"],
                success=args["success"],
                context=args.get("context", ""),
                notes=args.get("notes", ""),
            )
        elif name == "refine_procedure":
            return proc.refine_procedure(
                procedure_id=args["procedure_id"],
                correction=args["correction"],
            )
        elif name == "list_procedures":
            return proc.list_procedures(
                status=args.get("status", "active"),
                limit=args.get("limit", 20),
            )
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_procedural_tools"):
        server._procedural_tools = {}
    server._procedural_tools.update(tool_defs)
    server._procedural_handler = _handle
