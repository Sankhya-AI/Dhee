"""
engram MCP Server for Claude Code integration.

This server exposes engram's core memory capabilities as MCP tools.
Governance, handoff, and active memory tools live in engram-enterprise.
"""

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from engram.memory.main import Memory
from engram.configs.base import (
    MemoryConfig,
    VectorStoreConfig,
    LLMConfig,
    EmbedderConfig,
    FadeMemConfig,
)

logger = logging.getLogger(__name__)


def _get_embedding_dims_for_model(model: str, provider: str) -> int:
    """Get the embedding dimensions for a given model."""
    EMBEDDING_DIMS = {
        "models/text-embedding-005": 768,
        "text-embedding-005": 768,
        "models/text-embedding-004": 768,
        "text-embedding-004": 768,
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
    """Create and return a configured Memory instance."""
    gemini_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if gemini_key:
        embedder_model = os.environ.get("FADEM_EMBEDDER_MODEL", "gemini-embedding-001")
        embedding_dims = _get_embedding_dims_for_model(embedder_model, "gemini")

        llm_config = LLMConfig(
            provider="gemini",
            config={
                "model": os.environ.get("FADEM_LLM_MODEL", "gemini-2.0-flash"),
                "temperature": 0.1,
                "max_tokens": 1024,
                "api_key": gemini_key,
            }
        )
        embedder_config = EmbedderConfig(
            provider="gemini",
            config={
                "model": embedder_model,
                "api_key": gemini_key,
            }
        )
    elif openai_key:
        embedder_model = os.environ.get("FADEM_EMBEDDER_MODEL", "text-embedding-3-small")
        embedding_dims = _get_embedding_dims_for_model(embedder_model, "openai")

        llm_config = LLMConfig(
            provider="openai",
            config={
                "model": os.environ.get("FADEM_LLM_MODEL", "gpt-4o-mini"),
                "temperature": 0.1,
                "max_tokens": 1024,
                "api_key": openai_key,
            }
        )
        embedder_config = EmbedderConfig(
            provider="openai",
            config={
                "model": embedder_model,
                "api_key": openai_key,
            }
        )
    else:
        raise RuntimeError(
            "No API key found. Set GOOGLE_API_KEY, GEMINI_API_KEY, or OPENAI_API_KEY environment variable."
        )

    vec_db_path = os.environ.get(
        "FADEM_VEC_DB_PATH",
        os.path.join(os.path.expanduser("~"), ".engram", "sqlite_vec.db")
    )
    vector_store_config = VectorStoreConfig(
        provider="sqlite_vec",
        config={
            "path": vec_db_path,
            "collection_name": os.environ.get("FADEM_COLLECTION", "fadem_memories"),
            "embedding_model_dims": embedding_dims,
        }
    )

    history_db_path = os.environ.get(
        "FADEM_HISTORY_DB",
        os.path.join(os.path.expanduser("~"), ".engram", "history.db")
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

    return Memory(config)


# Global memory instance (lazy initialized)
_memory: Optional[Memory] = None


def get_memory() -> Memory:
    """Get or create the global memory instance."""
    global _memory
    if _memory is None:
        _memory = get_memory_instance()
    return _memory


# Create the MCP server
server = Server("engram-memory")

# Cached tool list
_tools_cache: Optional[List[Tool]] = None


@server.list_tools()
async def list_tools() -> List[Tool]:
    """List available engram tools."""
    global _tools_cache
    if _tools_cache is not None:
        return list(_tools_cache)
    tools = [
        Tool(
            name="add_memory",
            description="Store a memory. Extracts key information from the content and saves it with semantic embedding for later retrieval.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The memory content to store."
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent identifier (optional)"
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional categories to tag this memory with"
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata to attach"
                    },
                    "scope": {
                        "type": "string",
                        "description": "Confidentiality scope: work|personal|finance|health|private"
                    },
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="search_memory",
            description="Search for relevant memories by semantic query.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent identifier (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default: 10)"
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by categories"
                    },
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_all_memories",
            description="Get all stored memories for a user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent identifier (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum memories to return (default: 50)"
                    },
                    "layer": {
                        "type": "string",
                        "enum": ["sml", "lml"],
                        "description": "Filter by memory layer"
                    }
                }
            }
        ),
        Tool(
            name="get_memory",
            description="Retrieve a single memory by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The ID of the memory to retrieve"
                    }
                },
                "required": ["memory_id"]
            }
        ),
        Tool(
            name="update_memory",
            description="Update an existing memory's content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The ID of the memory to update"
                    },
                    "content": {
                        "type": "string",
                        "description": "The new content"
                    }
                },
                "required": ["memory_id", "content"]
            }
        ),
        Tool(
            name="delete_memory",
            description="Permanently delete a memory by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The ID of the memory to delete"
                    }
                },
                "required": ["memory_id"]
            }
        ),
        Tool(
            name="get_memory_stats",
            description="Get statistics about the memory store.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                }
            }
        ),
        Tool(
            name="apply_memory_decay",
            description="Apply the memory-decay algorithm to simulate natural forgetting.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                }
            }
        ),
        Tool(
            name="engram_context",
            description="Session-start digest. Returns top memories sorted by strength with LML first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer"},
                }
            }
        ),
        Tool(
            name="remember",
            description="Quick-save a fact or preference to memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact or preference to remember"
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["content"]
            }
        ),
        # ---- Episodic Scene tools ----
        Tool(
            name="get_scene",
            description="Get a specific episodic scene by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scene_id": {"type": "string"},
                    "user_id": {"type": "string"},
                },
                "required": ["scene_id"]
            }
        ),
        Tool(
            name="list_scenes",
            description="List episodic scenes chronologically.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "topic": {"type": "string"},
                    "start_after": {"type": "string"},
                    "start_before": {"type": "string"},
                    "limit": {"type": "integer"},
                }
            }
        ),
        Tool(
            name="search_scenes",
            description="Semantic search over episodic scene summaries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"]
            }
        ),
        # ---- Character Profile tools ----
        Tool(
            name="get_profile",
            description="Get a character profile by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile_id": {"type": "string"},
                },
                "required": ["profile_id"]
            }
        ),
        Tool(
            name="list_profiles",
            description="List all character profiles for a user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                }
            }
        ),
        Tool(
            name="search_profiles",
            description="Search character profiles by name or description.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"]
            }
        ),
        # ---- Handoff / Session Continuity tools ----
        Tool(
            name="get_last_session",
            description="Load previous session context for continuity. Returns task summary, decisions, files touched, and remaining TODOs from the last session. Falls back to parsing Claude Code conversation logs if no stored session exists.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                    "requester_agent_id": {"type": "string", "description": "ID of the agent requesting the session"},
                    "repo": {"type": "string", "description": "Absolute path to the repo root (enables log fallback)"},
                    "agent_id": {"type": "string", "description": "Source agent whose session to load (default: 'mcp-server')"},
                    "fallback_log_recovery": {"type": "boolean", "default": True, "description": "Fall back to JSONL log parsing if no bus session exists"},
                },
            }
        ),
        Tool(
            name="save_session_digest",
            description="Save session context for the next agent. Call on milestones and before pausing/ending.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_summary": {"type": "string", "description": "Summary of the current task"},
                    "repo": {"type": "string", "description": "Absolute path to the repo root"},
                    "status": {"type": "string", "enum": ["active", "paused", "completed"], "description": "Session status"},
                    "decisions_made": {"type": "array", "items": {"type": "string"}, "description": "Key decisions made during the session"},
                    "files_touched": {"type": "array", "items": {"type": "string"}, "description": "Files read, edited, or created"},
                    "todos_remaining": {"type": "array", "items": {"type": "string"}, "description": "Outstanding work items"},
                    "blockers": {"type": "array", "items": {"type": "string"}, "description": "Known blockers or issues"},
                    "key_commands": {"type": "array", "items": {"type": "string"}, "description": "Important commands run"},
                    "test_results": {"type": "string", "description": "Summary of test outcomes"},
                    "agent_id": {"type": "string", "description": "Agent saving the session (default: 'claude-code')"},
                    "requester_agent_id": {"type": "string", "description": "ID of the agent making the request"},
                },
                "required": ["task_summary"],
            }
        ),
        # ---- Task tools ----
        Tool(
            name="create_task",
            description="Create a task (with dedup — returns existing if title matches an active task).",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title (used for dedup)"},
                    "description": {"type": "string", "description": "Detailed description"},
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                    "status": {"type": "string", "enum": ["inbox", "assigned", "active", "review", "blocked"]},
                    "assigned_agent": {"type": "string", "description": "Agent to assign"},
                    "due_date": {"type": "string", "description": "ISO date string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "user_id": {"type": "string"},
                    "metadata": {"type": "object", "description": "Arbitrary user-defined attributes"},
                },
                "required": ["title"],
            }
        ),
        Tool(
            name="list_tasks",
            description="List tasks with optional filters (status, priority, assignee).",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["inbox", "assigned", "active", "review", "blocked", "done", "archived"]},
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                    "assigned_agent": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            }
        ),
        Tool(
            name="get_task",
            description="Get full task details by memory ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task memory ID"},
                },
                "required": ["task_id"],
            }
        ),
        Tool(
            name="update_task",
            description="Update task fields (status, priority, assignee, title, description, tags, due_date, or custom metadata).",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task memory ID"},
                    "status": {"type": "string", "enum": ["inbox", "assigned", "active", "review", "blocked", "done", "archived"]},
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                    "assigned_agent": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "due_date": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["task_id"],
            }
        ),
        Tool(
            name="complete_task",
            description="Mark a task as done (shorthand for update_task with status=done).",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task memory ID"},
                },
                "required": ["task_id"],
            }
        ),
        Tool(
            name="add_task_comment",
            description="Add a comment to a task.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task memory ID"},
                    "text": {"type": "string", "description": "Comment text"},
                    "agent": {"type": "string", "description": "Agent adding the comment"},
                },
                "required": ["task_id", "text"],
            }
        ),
        Tool(
            name="search_tasks",
            description="Semantic search over tasks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "user_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            }
        ),
        Tool(
            name="get_pending_tasks",
            description="Get actionable tasks (not done/archived).",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "assigned_agent": {"type": "string"},
                },
            }
        ),
    ]
    _tools_cache = tools
    return list(tools)


# Tool handler registry
_TOOL_HANDLERS: Dict[str, Callable] = {}


def _tool_handler(name: str):
    def decorator(fn):
        _TOOL_HANDLERS[name] = fn
        return fn
    return decorator


@_tool_handler("add_memory")
def _handle_add_memory(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    content = arguments.get("content", "")
    user_id = arguments.get("user_id", "default")
    return memory.add(
        messages=content,
        user_id=user_id,
        agent_id=arguments.get("agent_id"),
        categories=arguments.get("categories"),
        metadata=arguments.get("metadata"),
        scope=arguments.get("scope", "work"),
        source_app="mcp",
        infer=False,
    )


@_tool_handler("remember")
def _handle_remember(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    return memory.add(
        messages=arguments.get("content", ""),
        user_id="default",
        agent_id="claude-code",
        categories=arguments.get("categories"),
        source_app="claude-code",
        infer=False,
    )


@_tool_handler("search_memory")
def _handle_search_memory(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    try:
        limit = max(1, min(1000, int(arguments.get("limit", 10))))
    except (ValueError, TypeError):
        limit = 10
    result = memory.search(
        query=arguments.get("query", ""),
        user_id=arguments.get("user_id", "default"),
        agent_id=arguments.get("agent_id"),
        limit=limit,
        categories=arguments.get("categories"),
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


@_tool_handler("get_all_memories")
def _handle_get_all_memories(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    try:
        limit = max(1, min(1000, int(arguments.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50
    result = memory.get_all(
        user_id=arguments.get("user_id", "default"),
        agent_id=arguments.get("agent_id"),
        limit=limit,
        layer=arguments.get("layer"),
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


@_tool_handler("get_memory")
def _handle_get_memory(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    result = memory.get(arguments.get("memory_id", ""))
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


@_tool_handler("update_memory")
def _handle_update_memory(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    return memory.update(arguments.get("memory_id", ""), arguments.get("content", ""))


@_tool_handler("delete_memory")
def _handle_delete_memory(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    return memory.delete(arguments.get("memory_id", ""))


@_tool_handler("get_memory_stats")
def _handle_get_memory_stats(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    return memory.get_stats(
        user_id=arguments.get("user_id"),
        agent_id=arguments.get("agent_id"),
    )


@_tool_handler("apply_memory_decay")
def _handle_apply_memory_decay(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    user_id = arguments.get("user_id")
    agent_id = arguments.get("agent_id")
    scope = {"user_id": user_id, "agent_id": agent_id} if user_id or agent_id else None
    return memory.apply_decay(scope=scope)


@_tool_handler("engram_context")
def _handle_engram_context(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    user_id = arguments.get("user_id", "default")
    try:
        limit = max(1, min(100, int(arguments.get("limit", 15))))
    except (ValueError, TypeError):
        limit = 15
    all_result = memory.get_all(user_id=user_id, limit=limit * 3)
    all_memories = all_result.get("results", [])
    layer_order = {"lml": 0, "sml": 1}
    all_memories.sort(key=lambda m: (
        layer_order.get(m.get("layer", "sml"), 1),
        -float(m.get("strength", 1.0))
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
    # Surface pending tasks in context digest
    try:
        tm = _get_task_manager(memory)
        pending = tm.get_pending_tasks(user_id=user_id)
        pending_summary = [
            {"id": t["id"], "title": t["title"], "status": t["status"], "priority": t["priority"]}
            for t in pending[:5]
        ]
    except Exception:
        pending = []
        pending_summary = []

    return {
        "digest": digest,
        "total_in_store": len(all_memories),
        "returned": len(digest),
        "pending_tasks": pending_summary,
        "pending_task_count": len(pending),
    }


@_tool_handler("get_scene")
def _handle_get_scene(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    scene_id = arguments.get("scene_id", "")
    scene = memory.db.get_scene(scene_id)
    return scene if scene else {"error": "Scene not found"}


@_tool_handler("list_scenes")
def _handle_list_scenes(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    try:
        scene_limit = max(1, min(200, int(arguments.get("limit", 20))))
    except (ValueError, TypeError):
        scene_limit = 20
    scenes = memory.get_scenes(
        user_id=arguments.get("user_id", "default"),
        topic=arguments.get("topic"),
        start_after=arguments.get("start_after"),
        start_before=arguments.get("start_before"),
        limit=scene_limit,
    )
    return {
        "scenes": [
            {
                "id": s["id"],
                "title": s.get("title"),
                "topic": s.get("topic"),
                "summary": s.get("summary"),
                "start_time": s.get("start_time"),
                "end_time": s.get("end_time"),
                "memory_count": len(s.get("memory_ids", [])),
            }
            for s in scenes
        ],
        "total": len(scenes),
    }


@_tool_handler("search_scenes")
def _handle_search_scenes(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    try:
        limit = max(1, min(100, int(arguments.get("limit", 10))))
    except (ValueError, TypeError):
        limit = 10
    scenes = memory.search_scenes(
        query=arguments.get("query", ""),
        user_id=arguments.get("user_id", "default"),
        limit=limit,
    )
    return {
        "scenes": [
            {
                "id": s.get("id"),
                "title": s.get("title"),
                "summary": s.get("summary"),
                "topic": s.get("topic"),
                "start_time": s.get("start_time"),
                "memory_count": len(s.get("memory_ids", [])),
            }
            for s in scenes
        ],
        "total": len(scenes),
    }


@_tool_handler("get_profile")
def _handle_get_profile(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    profile = memory.get_profile(arguments.get("profile_id", ""))
    if profile:
        profile.pop("embedding", None)
        return profile
    return {"error": "Profile not found"}


@_tool_handler("list_profiles")
def _handle_list_profiles(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    profiles = memory.get_all_profiles(user_id=arguments.get("user_id", "default"))
    return {
        "profiles": [
            {
                "id": p["id"],
                "name": p.get("name"),
                "profile_type": p.get("profile_type"),
                "narrative": p.get("narrative"),
                "fact_count": len(p.get("facts", [])),
                "preference_count": len(p.get("preferences", [])),
            }
            for p in profiles
        ],
        "total": len(profiles),
    }


@_tool_handler("search_profiles")
def _handle_search_profiles(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    try:
        limit = max(1, min(100, int(arguments.get("limit", 10))))
    except (ValueError, TypeError):
        limit = 10
    profiles = memory.search_profiles(
        query=arguments.get("query", ""),
        user_id=arguments.get("user_id", "default"),
        limit=limit,
    )
    return {
        "profiles": [
            {
                "id": p["id"],
                "name": p.get("name"),
                "profile_type": p.get("profile_type"),
                "narrative": p.get("narrative"),
                "facts": p.get("facts", [])[:5],
                "search_score": p.get("search_score"),
            }
            for p in profiles
        ],
        "total": len(profiles),
    }


@_tool_handler("get_last_session")
def _handle_get_last_session(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    from engram.core.kernel import get_last_session
    agent_id = arguments.get("agent_id", "mcp-server")
    repo = arguments.get("repo")
    fallback = arguments.get("fallback_log_recovery", True)
    session = get_last_session(
        agent_id=agent_id,
        repo=repo,
        fallback_log_recovery=fallback,
    )
    if session is None:
        return {"status": "no_session", "message": "No previous session found."}
    return session


@_tool_handler("save_session_digest")
def _handle_save_session_digest(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    from engram.core.kernel import save_session_digest
    return save_session_digest(
        task_summary=arguments.get("task_summary", ""),
        agent_id=arguments.get("agent_id", "claude-code"),
        repo=arguments.get("repo"),
        status=arguments.get("status", "active"),
        decisions_made=arguments.get("decisions_made"),
        files_touched=arguments.get("files_touched"),
        todos_remaining=arguments.get("todos_remaining"),
        blockers=arguments.get("blockers"),
        key_commands=arguments.get("key_commands"),
        test_results=arguments.get("test_results"),
    )


# ---- Task tool handlers ----

def _get_task_manager(memory: "Memory"):
    from engram.memory.tasks import TaskManager
    return TaskManager(memory)


@_tool_handler("create_task")
def _handle_create_task(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    tm = _get_task_manager(memory)
    return tm.create_task(
        title=arguments.get("title", ""),
        description=arguments.get("description", ""),
        priority=arguments.get("priority"),
        status=arguments.get("status", "inbox"),
        assignee=arguments.get("assigned_agent"),
        due_date=arguments.get("due_date"),
        tags=arguments.get("tags"),
        user_id=arguments.get("user_id", "default"),
        extra_metadata=arguments.get("metadata"),
    )


@_tool_handler("list_tasks")
def _handle_list_tasks(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    tm = _get_task_manager(memory)
    try:
        limit = max(1, min(500, int(arguments.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50
    tasks = tm.list_tasks(
        user_id=arguments.get("user_id", "default"),
        status=arguments.get("status"),
        priority=arguments.get("priority"),
        assignee=arguments.get("assigned_agent"),
        limit=limit,
    )
    return {"tasks": tasks, "total": len(tasks)}


@_tool_handler("get_task")
def _handle_get_task(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    tm = _get_task_manager(memory)
    task = tm.get_task(arguments.get("task_id", ""))
    return task if task else {"error": "Task not found"}


@_tool_handler("update_task")
def _handle_update_task(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    tm = _get_task_manager(memory)
    task_id = arguments.pop("task_id", "")
    updates = {k: v for k, v in arguments.items() if v is not None}
    result = tm.update_task(task_id, updates)
    return result if result else {"error": "Task not found"}


@_tool_handler("complete_task")
def _handle_complete_task(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    tm = _get_task_manager(memory)
    result = tm.complete_task(arguments.get("task_id", ""))
    return result if result else {"error": "Task not found"}


@_tool_handler("add_task_comment")
def _handle_add_task_comment(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    tm = _get_task_manager(memory)
    result = tm.add_comment(
        task_id=arguments.get("task_id", ""),
        agent=arguments.get("agent", "unknown"),
        text=arguments.get("text", ""),
    )
    return result if result else {"error": "Task not found"}


@_tool_handler("search_tasks")
def _handle_search_tasks(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    tm = _get_task_manager(memory)
    try:
        limit = max(1, min(100, int(arguments.get("limit", 10))))
    except (ValueError, TypeError):
        limit = 10
    tasks = tm.search_tasks(
        query=arguments.get("query", ""),
        user_id=arguments.get("user_id", "default"),
        limit=limit,
    )
    return {"tasks": tasks, "total": len(tasks)}


@_tool_handler("get_pending_tasks")
def _handle_get_pending_tasks(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    tm = _get_task_manager(memory)
    tasks = tm.get_pending_tasks(
        user_id=arguments.get("user_id", "default"),
        assignee=arguments.get("assigned_agent"),
    )
    return {"tasks": tasks, "total": len(tasks)}


_MEMORY_FREE_TOOLS = {"get_last_session", "save_session_digest"}


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle tool calls."""
    try:
        memory = None if name in _MEMORY_FREE_TOOLS else get_memory()

        handler = _TOOL_HANDLERS.get(name)
        if handler:
            result = handler(memory, arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as e:
        logger.exception("MCP tool '%s' failed", name)
        error_msg = f"{type(e).__name__}: {e}"
        return [TextContent(type="text", text=json.dumps({"error": error_msg}, indent=2))]


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
