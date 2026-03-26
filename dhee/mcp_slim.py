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
# Lazy singletons
# ---------------------------------------------------------------------------

_memory = None
_buddhi = None


def _get_memory():
    """Create memory instance with deferred enrichment (0 LLM on hot path)."""
    global _memory
    if _memory is None:
        from dhee.mcp_server import get_memory_instance
        _memory = get_memory_instance()
        # Enable deferred enrichment: 0 LLM calls at ingestion,
        # batch-enrich later at checkpoint time for retrieval quality.
        if hasattr(_memory, "config") and hasattr(_memory.config, "enrichment"):
            _memory.config.enrichment.defer_enrichment = True
            _memory.config.enrichment.enable_unified = True
    return _memory


def _get_buddhi():
    global _buddhi
    if _buddhi is None:
        from dhee.core.buddhi import Buddhi
        _buddhi = Buddhi()
    return _buddhi


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
    """Store a memory. 0 LLM calls on hot path, 1 embed. Enrichment deferred."""
    memory = _get_memory()
    content = args.get("content", "")
    if not content:
        return {"error": "content is required"}

    user_id = args.get("user_id", "default")

    # infer=False: agent explicitly stated the fact, no need to re-extract.
    # defer_enrichment (set in _get_memory): echo/keywords added at checkpoint.
    result = memory.add(
        messages=content,
        user_id=user_id,
        agent_id="agent",
        source_app="dhee-mcp",
        infer=False,
    )

    # Buddhi: detect intentions in the content
    buddhi = _get_buddhi()
    intention = buddhi.on_memory_stored(content=content, user_id=user_id)

    response: Dict[str, Any] = {"stored": True}
    if isinstance(result, dict):
        results = result.get("results", [])
        if results:
            response["id"] = results[0].get("id")
    if intention:
        response["detected_intention"] = intention.to_dict()
    return response


def _handle_recall(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search memory. 0 LLM calls, 1 embed."""
    memory = _get_memory()
    query = args.get("query", "")
    if not query:
        return {"error": "query is required"}

    user_id = args.get("user_id", "default")
    limit = min(max(1, int(args.get("limit", 5))), 20)

    result = memory.search(
        query=query,
        user_id=user_id,
        limit=limit,
    )
    results = result.get("results", [])

    # Compact output — only what the agent needs
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
    buddhi_signals = result.get("buddhi")
    if buddhi_signals:
        response["proactive"] = buddhi_signals

    return response


def _handle_context(args: Dict[str, Any]) -> Dict[str, Any]:
    """HyperAgent bootstrap. Buddhi-powered."""
    memory = _get_memory()
    buddhi = _get_buddhi()
    user_id = args.get("user_id", "default")
    task_description = args.get("task_description")

    hyper_ctx = buddhi.get_hyper_context(
        user_id=user_id,
        task_description=task_description,
        memory=memory,
    )
    return hyper_ctx.to_dict()


def _handle_checkpoint(args: Dict[str, Any]) -> Dict[str, Any]:
    """Session lifecycle — save digest + enrich + outcome + reflect + intention."""
    summary = args.get("summary", "")
    if not summary:
        return {"error": "summary is required"}

    user_id = args.get("user_id", "default")
    agent_id = args.get("agent_id", "agent")
    result: Dict[str, Any] = {}

    # 1. Save session digest (for handoff)
    try:
        from dhee.core.kernel import save_session_digest
        digest = save_session_digest(
            task_summary=summary,
            agent_id=agent_id,
            repo=args.get("repo"),
            status=args.get("status", "paused"),
            decisions_made=args.get("decisions"),
            files_touched=args.get("files_touched"),
            todos_remaining=args.get("todos"),
        )
        result["session_saved"] = True
        if isinstance(digest, dict):
            result["session_id"] = digest.get("session_id")
    except Exception as e:
        logger.debug("Session save skipped: %s", e)
        result["session_saved"] = False

    # 2. Batch-enrich deferred memories (1 LLM call per ~10 memories)
    # This is where retrieval quality gets added — echo paraphrases, keywords,
    # categories — all in one batched LLM call. Not on the hot path.
    memory = _get_memory()
    if hasattr(memory, "enrich_pending"):
        try:
            enrich_result = memory.enrich_pending(
                user_id=user_id, batch_size=10, max_batches=5,
            )
            enriched = enrich_result.get("enriched_count", 0)
            if enriched > 0:
                result["memories_enriched"] = enriched
        except Exception as e:
            logger.debug("Batch enrichment skipped: %s", e)

    buddhi = _get_buddhi()

    # 3. Record outcome (for performance tracking)
    task_type = args.get("task_type")
    outcome_score = args.get("outcome_score")
    if task_type and outcome_score is not None:
        score = max(0.0, min(1.0, float(outcome_score)))
        insight = buddhi.record_outcome(
            user_id=user_id, task_type=task_type, score=score,
        )
        result["outcome_recorded"] = True
        if insight:
            result["auto_insight"] = insight.to_dict()

    # 4. Reflect (for insight synthesis)
    what_worked = args.get("what_worked")
    what_failed = args.get("what_failed")
    key_decision = args.get("key_decision")
    if any([what_worked, what_failed, key_decision]):
        reflections = buddhi.reflect(
            user_id=user_id,
            task_type=task_type or "general",
            what_worked=what_worked,
            what_failed=what_failed,
            key_decision=key_decision,
        )
        result["insights_created"] = len(reflections)

    # 5. Store intention (for prospective memory)
    remember_to = args.get("remember_to")
    if remember_to:
        intention = buddhi.store_intention(
            user_id=user_id,
            description=remember_to,
            trigger_keywords=args.get("trigger_keywords"),
        )
        result["intention_stored"] = intention.to_dict()

    return result


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
