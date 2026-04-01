"""Dhee — 4-tool MCP server. Cognition as a Service.

Install: pip install dhee[openai,mcp]
Config:  export OPENAI_API_KEY=sk-...
Run:     dhee-mcp

Tools:
 1. remember    — Store a fact (0 LLM on hot path, 1 embed). Enrichment deferred to checkpoint.
 2. recall      — Search memory, get top-K results (0 LLM, 1 embed)
 3. context     — HyperAgent bootstrap: performance + insights + intentions + memories
 4. checkpoint  — Save session + batch-enrich stored memories (1 LLM per ~10 memories)

Cost model:
  Hot path (remember/recall): ~$0.0002 per call (1 embedding only)
  Checkpoint: ~$0.001 per 10 memories enriched (1 LLM batch call)
  Enrichment adds echo paraphrases + keywords → dramatically better recall quality.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton — DheePlugin wraps Engram + Buddhi
# ---------------------------------------------------------------------------

_plugin = None


def _get_plugin():
    """Create the DheePlugin singleton. Wraps Engram + Buddhi."""
    global _plugin
    if _plugin is None:
        from dhee.adapters.base import DheePlugin
        _plugin = DheePlugin()
        # Enable deferred enrichment on the underlying memory
        memory = _plugin.memory
        if hasattr(memory, "config") and hasattr(memory.config, "enrichment"):
            memory.config.enrichment.defer_enrichment = True
            memory.config.enrichment.enable_unified = True

        # Auto-checkpoint on server shutdown
        import atexit
        def _auto_checkpoint_on_exit():
            try:
                args = _plugin._tracker.finalize()
                if args:
                    result = _plugin.checkpoint(**args)
                    for warning in result.get("warnings", []):
                        logger.warning("MCP auto-checkpoint warning: %s", warning)
            except Exception as exc:
                logger.warning("MCP auto-checkpoint on exit failed: %s", exc, exc_info=True)
        atexit.register(_auto_checkpoint_on_exit)

    return _plugin


# ---------------------------------------------------------------------------
# 4 Tools
# ---------------------------------------------------------------------------

server = Server("dhee")

TOOLS = [
    Tool(
        name="remember",
        description=(
            "Store a fact, preference, or conversation context to memory. "
            "Fast: 0 LLM calls on hot path, 1 embedding. "
            "Echo enrichment (paraphrases, keywords) runs at checkpoint for better recall quality. "
            "Examples: 'User prefers dark mode', 'Project uses FastAPI + PostgreSQL'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact or preference to remember",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier (default: 'default')",
                },
            },
            "required": ["content"],
        },
    ),
    Tool(
        name="recall",
        description=(
            "Search memory for relevant facts. Returns top-K results ranked by relevance. "
            "Lightweight: 0 LLM calls, 1 embedding call. "
            "Use for: 'What does the user prefer?', 'What did we discuss about X?'"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you're trying to remember",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier (default: 'default')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 5)",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="context",
        description=(
            "HyperAgent session bootstrap. Call ONCE at conversation start. "
            "Returns: last session state, performance trends, synthesized insights, "
            "pending intentions, proactive warnings, and top memories. "
            "This single call gives you everything you need to continue where you left off."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "What you're about to work on (filters relevant context)",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier (default: 'default')",
                },
                "operational": {
                    "type": "boolean",
                    "description": "If true, return compact actionable-only format for per-turn use (default: false)",
                },
            },
        },
    ),
    Tool(
        name="checkpoint",
        description=(
            "Save session state and learnings before ending a conversation. "
            "Also batch-enriches any memories stored since last checkpoint (1 LLM call per ~10 memories) "
            "to add echo paraphrases and keywords for better future recall. "
            "Combines: session digest, batch enrichment, outcome recording, reflection, and intention storage. "
            "Include whatever fields are relevant — all are optional except summary."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "What you were working on (the task)",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "completed"],
                    "description": "Session status (default: 'paused')",
                },
                "decisions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key decisions made during the session",
                },
                "todos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Remaining work items",
                },
                "files_touched": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files modified during the session",
                },
                "outcome_score": {
                    "type": "number",
                    "description": "Task outcome score 0.0-1.0 (for performance tracking)",
                },
                "task_type": {
                    "type": "string",
                    "description": "Category of the task (e.g., 'bug_fix', 'refactor')",
                },
                "what_worked": {
                    "type": "string",
                    "description": "What approach worked well (becomes transferable insight)",
                },
                "what_failed": {
                    "type": "string",
                    "description": "What approach failed (becomes a warning for future runs)",
                },
                "key_decision": {
                    "type": "string",
                    "description": "A key decision and its rationale",
                },
                "remember_to": {
                    "type": "string",
                    "description": "Future intention — 'remember to X when Y' (prospective memory)",
                },
                "trigger_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords that trigger the intention",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository/project path for scoping",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier (default: 'default')",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier (default: 'agent')",
                },
            },
            "required": ["summary"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_remember(args: Dict[str, Any]) -> Dict[str, Any]:
    """Store a memory. Delegates to DheePlugin.remember()."""
    content = args.get("content", "")
    if not content:
        return {"error": "content is required"}
    return _get_plugin().remember(
        content=content,
        user_id=args.get("user_id", "default"),
    )


def _handle_recall(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search memory. 0 LLM calls, 1 embed."""
    query = args.get("query", "")
    if not query:
        return {"error": "query is required"}

    plugin = _get_plugin()
    user_id = args.get("user_id", "default")
    limit = min(max(1, int(args.get("limit", 5))), 20)

    # Use raw memory search to get proactive signals alongside results
    raw_result = plugin._engram._memory.search(
        query=query, user_id=user_id, limit=limit,
    )
    results = raw_result.get("results", []) if isinstance(raw_result, dict) else []

    memories = [
        {
            "id": r.get("id"),
            "memory": r.get("memory", ""),
            "score": round(r.get("composite_score", r.get("score", 0)), 3),
        }
        for r in results
    ]

    response: Dict[str, Any] = {"memories": memories, "count": len(memories)}

    # Attach Buddhi proactive signals if any
    buddhi_signals = raw_result.get("buddhi") if isinstance(raw_result, dict) else None
    if buddhi_signals:
        response["proactive"] = buddhi_signals

    return response


def _handle_context(args: Dict[str, Any]) -> Dict[str, Any]:
    """HyperAgent bootstrap. Delegates to DheePlugin.context()."""
    return _get_plugin().context(
        task_description=args.get("task_description"),
        user_id=args.get("user_id", "default"),
        operational=bool(args.get("operational", False)),
    )


def _handle_checkpoint(args: Dict[str, Any]) -> Dict[str, Any]:
    """Session lifecycle. Delegates to DheePlugin.checkpoint()."""
    summary = args.get("summary", "")
    if not summary:
        return {"error": "summary is required"}

    return _get_plugin().checkpoint(
        summary=summary,
        task_type=args.get("task_type"),
        outcome_score=args.get("outcome_score"),
        what_worked=args.get("what_worked"),
        what_failed=args.get("what_failed"),
        key_decision=args.get("key_decision"),
        remember_to=args.get("remember_to"),
        trigger_keywords=args.get("trigger_keywords"),
        status=args.get("status", "paused"),
        decisions=args.get("decisions"),
        todos=args.get("todos"),
        files_touched=args.get("files_touched"),
        repo=args.get("repo"),
        user_id=args.get("user_id", "default"),
        agent_id=args.get("agent_id", "agent"),
    )


HANDLERS = {
    "remember": _handle_remember,
    "recall": _handle_recall,
    "context": _handle_context,
    "checkpoint": _handle_checkpoint,
}


# ---------------------------------------------------------------------------
# MCP Protocol
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> List[Tool]:
    return list(TOOLS)


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    try:
        handler = HANDLERS.get(name)
        if not handler:
            result = {"error": f"Unknown tool: {name}"}
        else:
            result = handler(arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
    except Exception as e:
        logger.exception("Tool '%s' failed", name)
        return [TextContent(
            type="text",
            text=json.dumps({"error": f"{type(e).__name__}: {e}"}, indent=2),
        )]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run():
    """Entry point: dhee-mcp"""
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    run()
