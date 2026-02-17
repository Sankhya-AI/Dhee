"""Engram MCP Server — 8 tools, minimal boilerplate.

Tools:
1. remember        — Quick-save (content → memory, infer=False)
2. search_memory   — Semantic search
3. get_memory      — Fetch by ID
4. get_all_memories — List with filters
5. engram_context  — Session-start digest (top memories)
6. get_last_session — Handoff: load prior session
7. save_session_digest — Handoff: save current session
8. get_memory_stats — Quick health check
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from engram.memory.main import FullMemory, Memory
from engram.configs.base import (
    MemoryConfig,
    VectorStoreConfig,
    LLMConfig,
    EmbedderConfig,
    FadeMemConfig,
)

logger = logging.getLogger(__name__)


def _get_embedding_dims_for_model(model: str, provider: str) -> int:
    EMBEDDING_DIMS = {
        "models/text-embedding-005": 768,
        "text-embedding-005": 768,
        "gemini-embedding-001": 3072,
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }
    env_dims = os.environ.get("FADEM_EMBEDDING_DIMS")
    if env_dims:
        return int(env_dims)
    if model in EMBEDDING_DIMS:
        return EMBEDDING_DIMS[model]
    if provider == "gemini":
        return 3072
    elif provider == "openai":
        return 1536
    return 3072


def get_memory_instance() -> Memory:
    """Create and return a configured Memory instance (FullMemory for MCP)."""
    gemini_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if gemini_key:
        embedder_model = os.environ.get("FADEM_EMBEDDER_MODEL", "gemini-embedding-001")
        embedding_dims = _get_embedding_dims_for_model(embedder_model, "gemini")
        llm_config = LLMConfig(
            provider="gemini",
            config={
                "model": os.environ.get("FADEM_LLM_MODEL", "gemini-2.0-flash"),
                "temperature": 0.1, "max_tokens": 1024, "api_key": gemini_key,
            }
        )
        embedder_config = EmbedderConfig(
            provider="gemini",
            config={"model": embedder_model, "api_key": gemini_key},
        )
    elif openai_key:
        embedder_model = os.environ.get("FADEM_EMBEDDER_MODEL", "text-embedding-3-small")
        embedding_dims = _get_embedding_dims_for_model(embedder_model, "openai")
        llm_config = LLMConfig(
            provider="openai",
            config={
                "model": os.environ.get("FADEM_LLM_MODEL", "gpt-4o-mini"),
                "temperature": 0.1, "max_tokens": 1024, "api_key": openai_key,
            }
        )
        embedder_config = EmbedderConfig(
            provider="openai",
            config={"model": embedder_model, "api_key": openai_key},
        )
    else:
        # Zero-config: SimpleEmbedder + MockLLM
        embedding_dims = 384
        llm_config = LLMConfig(provider="mock", config={})
        embedder_config = EmbedderConfig(
            provider="simple", config={"embedding_dims": 384},
        )

    vec_db_path = os.environ.get(
        "FADEM_VEC_DB_PATH",
        os.path.join(os.path.expanduser("~"), ".engram", "sqlite_vec.db"),
    )

    # Use in-memory vector store for simple embedder (dims mismatch with sqlite_vec)
    if embedder_config.provider == "simple":
        vector_store_config = VectorStoreConfig(
            provider="memory",
            config={
                "collection_name": os.environ.get("FADEM_COLLECTION", "fadem_memories"),
                "embedding_model_dims": embedding_dims,
            },
        )
    else:
        vector_store_config = VectorStoreConfig(
            provider="sqlite_vec",
            config={
                "path": vec_db_path,
                "collection_name": os.environ.get("FADEM_COLLECTION", "fadem_memories"),
                "embedding_model_dims": embedding_dims,
            },
        )

    history_db_path = os.environ.get(
        "FADEM_HISTORY_DB",
        os.path.join(os.path.expanduser("~"), ".engram", "history.db"),
    )

    fadem_config = FadeMemConfig(
        enable_forgetting=os.environ.get("FADEM_ENABLE_FORGETTING", "true").lower() == "true",
        sml_decay_rate=float(os.environ.get("FADEM_SML_DECAY_RATE", "0.15")),
        lml_decay_rate=float(os.environ.get("FADEM_LML_DECAY_RATE", "0.02")),
    )

    config = MemoryConfig(
        vector_store=vector_store_config,
        llm=llm_config,
        embedder=embedder_config,
        history_db_path=history_db_path,
        embedding_model_dims=embedding_dims,
        engram=fadem_config,
    )

    return FullMemory(config)


# Global memory instance (lazy)
_memory: Optional[Memory] = None


def get_memory() -> Memory:
    global _memory
    if _memory is None:
        _memory = get_memory_instance()
    return _memory


# ── MCP Server ──

server = Server("engram-memory")

# Tool definitions — 8 tools total
TOOLS = [
    Tool(
        name="remember",
        description="Quick-save a fact or preference to memory. Creates a staging proposal commit with source_app='claude-code' and infer=False by default.",
        inputSchema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact or preference to remember"},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Optional categories to tag this memory with (e.g., ['preferences', 'coding'])"},
            },
            "required": ["content"],
        },
    ),
    Tool(
        name="search_memory",
        description="Search engram for relevant memories by semantic query. The UserPromptSubmit hook handles background search automatically — call this tool only for explicit user recall requests such as 'what did we discuss about X?' or 'recall my preference for Y'.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query - what you're trying to remember"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "agent_id": {"type": "string", "description": "Agent identifier to scope search to (optional)"},
                "limit": {"type": "integer", "description": "Maximum number of results to return (default: 10)"},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Filter results by categories"},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_memory",
        description="Retrieve a single memory by its ID. Use this only when you already have a memory_id from a prior search or listing. Do not use for discovery — use search_memory instead.",
        inputSchema={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "The ID of the memory to retrieve"},
            },
            "required": ["memory_id"],
        },
    ),
    Tool(
        name="get_all_memories",
        description="Get all stored memories for a user — use for inventory, audit, or when the user wants a full listing. Not for finding specific information; use search_memory for that.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "agent_id": {"type": "string", "description": "Agent identifier (optional)"},
                "limit": {"type": "integer", "description": "Maximum number of memories to return (default: 50)"},
                "layer": {"type": "string", "enum": ["sml", "lml"], "description": "Filter by memory layer: 'sml' (short-term) or 'lml' (long-term)"},
            },
        },
    ),
    Tool(
        name="engram_context",
        description="Session-start digest. Call once at the beginning of a new conversation to load context from prior sessions. Returns top memories sorted by strength with long-term memories (LML) first.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier to load context for (default: 'default')"},
                "limit": {"type": "integer", "description": "Maximum number of memories to return in the digest (default: 15)"},
            },
        },
    ),
    Tool(
        name="get_last_session",
        description="Get the most recent session digest to continue where the last agent left off. Returns full handoff context including linked memories.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "requester_agent_id": {"type": "string", "description": "Agent identity performing this read."},
                "repo": {"type": "string", "description": "Filter by repository/project path"},
                "agent_id": {"type": "string", "description": "Filter by source agent identifier"},
                "fallback_log_recovery": {"type": "boolean", "default": True, "description": "When true and no DB session found, attempt to reconstruct context from Claude Code conversation logs. Default: true."},
            },
        },
    ),
    Tool(
        name="save_session_digest",
        description="Save a session digest before ending or when interrupted. Enables cross-agent handoff so another agent can continue where you left off.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_summary": {"type": "string", "description": "What was the agent doing — the main task being worked on"},
                "repo": {"type": "string", "description": "Repository or project path for scoping"},
                "status": {"type": "string", "enum": ["active", "paused", "completed", "abandoned"], "description": "Session status (default: 'paused')"},
                "decisions_made": {"type": "array", "items": {"type": "string"}, "description": "Key decisions made during the session"},
                "files_touched": {"type": "array", "items": {"type": "string"}, "description": "File paths modified during the session"},
                "todos_remaining": {"type": "array", "items": {"type": "string"}, "description": "Remaining work items for the next agent"},
                "blockers": {"type": "array", "items": {"type": "string"}, "description": "Known blockers for the receiving agent"},
                "key_commands": {"type": "array", "items": {"type": "string"}, "description": "Important commands run during the session"},
                "test_results": {"type": "array", "items": {"type": "string"}, "description": "Recent test outcomes"},
                "agent_id": {"type": "string", "description": "Identifier of the agent saving the digest (default: 'claude-code')"},
                "requester_agent_id": {"type": "string", "description": "Agent identity performing this write (defaults to agent_id)."},
            },
            "required": ["task_summary"],
        },
    ),
    Tool(
        name="get_memory_stats",
        description="Get statistics about the memory store including counts and layer distribution. Call when the user asks about memory health, wants an overview of what's stored, or runs /engram:status.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier to get stats for (default: all users)"},
                "agent_id": {"type": "string", "description": "Agent identifier to scope stats to (optional)"},
            },
        },
    ),
]


# ── Tool Handlers ──

def _handle_remember(memory, args):
    return memory.add(
        messages=args.get("content", ""),
        user_id="default",
        agent_id="claude-code",
        categories=args.get("categories"),
        source_app="claude-code",
        infer=False,
    )


def _handle_search_memory(memory, args):
    try:
        limit = max(1, min(1000, int(args.get("limit", 10))))
    except (ValueError, TypeError):
        limit = 10
    result = memory.search(
        query=args.get("query", ""),
        user_id=args.get("user_id", "default"),
        agent_id=args.get("agent_id"),
        limit=limit,
        categories=args.get("categories"),
    )
    if "results" in result:
        result["results"] = [
            {
                "id": r.get("id"),
                "memory": r.get("memory", r.get("details", "")),
                "score": round(r.get("composite_score", r.get("score", 0)), 3),
                "layer": r.get("layer", "sml"),
                "categories": r.get("categories", []),
            }
            for r in result["results"]
        ]
    return result


def _handle_get_memory(memory, args):
    result = memory.get(args.get("memory_id", ""))
    if result:
        return {
            "id": result["id"],
            "memory": result["memory"],
            "layer": result.get("layer", "sml"),
            "strength": round(result.get("strength", 1.0), 3),
            "categories": result.get("categories", []),
            "created_at": result.get("created_at"),
            "access_count": result.get("access_count", 0),
        }
    return {"error": "Memory not found"}


def _handle_get_all_memories(memory, args):
    try:
        limit = max(1, min(1000, int(args.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50
    result = memory.get_all(
        user_id=args.get("user_id", "default"),
        agent_id=args.get("agent_id"),
        limit=limit,
        layer=args.get("layer"),
    )
    if "results" in result:
        result["results"] = [
            {
                "id": r["id"],
                "memory": r["memory"],
                "layer": r.get("layer", "sml"),
                "strength": round(r.get("strength", 1.0), 3),
                "categories": r.get("categories", []),
            }
            for r in result["results"]
        ]
    return result


def _handle_engram_context(memory, args):
    user_id = args.get("user_id", "default")
    try:
        limit = max(1, min(100, int(args.get("limit", 15))))
    except (ValueError, TypeError):
        limit = 15
    all_result = memory.get_all(user_id=user_id, limit=limit * 3)
    all_memories = all_result.get("results", [])
    layer_order = {"lml": 0, "sml": 1}
    all_memories.sort(key=lambda m: (
        layer_order.get(m.get("layer", "sml"), 1),
        -float(m.get("strength", 1.0)),
    ))
    digest = [
        {
            "id": m["id"],
            "memory": m.get("memory", ""),
            "layer": m.get("layer", "sml"),
            "strength": round(float(m.get("strength", 1.0)), 3),
            "categories": m.get("categories", []),
        }
        for m in all_memories[:limit]
    ]
    return {
        "digest": digest,
        "total_in_store": len(all_memories),
        "returned": len(digest),
    }


def _handle_get_last_session(_memory, args):
    from engram.core.kernel import get_last_session
    session = get_last_session(
        agent_id=args.get("agent_id", "mcp-server"),
        repo=args.get("repo"),
        fallback_log_recovery=args.get("fallback_log_recovery", True),
    )
    if session is None:
        return {"status": "no_session", "message": "No previous session found."}
    return session


def _handle_save_session_digest(_memory, args):
    from engram.core.kernel import save_session_digest
    return save_session_digest(
        task_summary=args.get("task_summary", ""),
        agent_id=args.get("agent_id", "claude-code"),
        repo=args.get("repo"),
        status=args.get("status", "active"),
        decisions_made=args.get("decisions_made"),
        files_touched=args.get("files_touched"),
        todos_remaining=args.get("todos_remaining"),
        blockers=args.get("blockers"),
        key_commands=args.get("key_commands"),
        test_results=args.get("test_results"),
    )


def _handle_get_memory_stats(memory, args):
    return memory.get_stats(
        user_id=args.get("user_id"),
        agent_id=args.get("agent_id"),
    )


HANDLERS = {
    "remember": _handle_remember,
    "search_memory": _handle_search_memory,
    "get_memory": _handle_get_memory,
    "get_all_memories": _handle_get_all_memories,
    "engram_context": _handle_engram_context,
    "get_last_session": _handle_get_last_session,
    "save_session_digest": _handle_save_session_digest,
    "get_memory_stats": _handle_get_memory_stats,
}

_MEMORY_FREE_TOOLS = {"get_last_session", "save_session_digest"}


@server.list_tools()
async def list_tools() -> List[Tool]:
    return list(TOOLS)


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    try:
        memory = None if name in _MEMORY_FREE_TOOLS else get_memory()
        handler = HANDLERS.get(name)
        if not handler:
            result = {"error": f"Unknown tool: {name}"}
        else:
            result = handler(memory, arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
    except Exception as e:
        logger.exception("MCP tool '%s' failed", name)
        return [TextContent(type="text", text=json.dumps({"error": f"{type(e).__name__}: {e}"}, indent=2))]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run():
    """Entry point for the MCP server."""
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    run()
