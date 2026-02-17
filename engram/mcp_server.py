"""
engram MCP Server for Claude Code integration.

This server exposes engram's core memory capabilities as MCP tools.
Governance, handoff, and active memory tools live in engram-enterprise.
"""

import importlib
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


# ── Power Package Auto-Discovery ──

_POWER_PACKAGES = [
    ("engram_router", "engram_router.mcp_tools"),
    ("engram_identity", "engram_identity.mcp_tools"),
    ("engram_heartbeat", "engram_heartbeat.mcp_tools"),
    ("engram_policy", "engram_policy.mcp_tools"),
    ("engram_skills", "engram_skills.mcp_tools"),
    ("engram_spawn", "engram_spawn.mcp_tools"),
    ("engram_resilience", "engram_resilience.mcp_tools"),
    ("engram_metamemory", "engram_metamemory.mcp_tools"),
    ("engram_prospective", "engram_prospective.mcp_tools"),
    ("engram_procedural", "engram_procedural.mcp_tools"),
    ("engram_reconsolidation", "engram_reconsolidation.mcp_tools"),
    ("engram_failure", "engram_failure.mcp_tools"),
    ("engram_working", "engram_working.mcp_tools"),
    ("engram_warroom", "engram_warroom.mcp_tools"),
]

_POWER_HANDLER_MAP = [
    ("_router_tools", "_router_handlers", "dict"),
    ("_identity_tools", "_identity_handlers", "fn"),
    ("_heartbeat_tools", "_heartbeat_handler", "fn"),
    ("_policy_tools", "_policy_handler", "fn"),
    ("_skills_tools", "_skills_handler", "fn"),
    ("_spawn_tools", "_spawn_handler", "fn"),
    ("_resilience_tools", "_resilience_handler", "fn"),
    ("_metamemory_tools", "_metamemory_handler", "fn"),
    ("_prospective_tools", "_prospective_handler", "fn"),
    ("_procedural_tools", "_procedural_handler", "fn"),
    ("_reconsolidation_tools", "_reconsolidation_handler", "fn"),
    ("_failure_tools", "_failure_handler", "fn"),
    ("_working_tools", "_working_handler", "fn"),
    ("_warroom_tools", "_warroom_handler", "fn"),
]

_power_tool_handlers: Dict[str, Callable] = {}
_power_discovered = False


def _discover_power_tools(srv: Server, memory: "Memory") -> None:
    """Auto-discover and register MCP tools from installed power packages."""
    global _power_discovered
    if _power_discovered:
        return
    _power_discovered = True

    for pkg_name, module_path in _POWER_PACKAGES:
        try:
            mod = importlib.import_module(module_path)
            mod.register_tools(srv, memory)
            logger.info("Loaded MCP tools from %s", pkg_name)
        except ImportError:
            pass  # package not installed — skip
        except Exception as e:
            logger.warning("Failed to load MCP tools from %s: %s", pkg_name, e)

    # Consolidate all handlers into a single dispatch dict
    for tools_attr, handler_attr, handler_type in _POWER_HANDLER_MAP:
        tool_defs = getattr(srv, tools_attr, None)
        handler = getattr(srv, handler_attr, None)
        if not tool_defs or not handler:
            continue
        if handler_type == "dict":
            for name in tool_defs:
                if name in handler:
                    _power_tool_handlers[name] = handler[name]
        else:
            for name in tool_defs:
                _power_tool_handlers[name] = (lambda h, n: lambda args: h(n, args))(handler, name)


def _get_power_tool_defs() -> Dict[str, dict]:
    """Collect all power tool definitions from server attributes."""
    defs = {}
    for tools_attr, _, _ in _POWER_HANDLER_MAP:
        tool_dict = getattr(server, tools_attr, None)
        if tool_dict:
            defs.update(tool_dict)
    return defs


@server.list_tools()
async def list_tools() -> List[Tool]:
    """List available engram tools."""
    global _tools_cache
    if _tools_cache is not None:
        return list(_tools_cache)

    # Auto-discover power packages
    try:
        memory = get_memory()
        _discover_power_tools(server, memory)
    except Exception as e:
        logger.warning("Power tool discovery failed: %s", e)

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
        # ---- Salience tools ----
        Tool(
            name="tag_salience",
            description="Compute and tag a memory's emotional salience (valence + arousal).",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "Memory ID to tag"},
                    "use_llm": {"type": "boolean", "description": "Use LLM for more accurate scoring", "default": False},
                },
                "required": ["memory_id"],
            }
        ),
        Tool(
            name="search_by_salience",
            description="Find high-salience (emotionally significant) memories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "min_salience": {"type": "number", "description": "Minimum salience score (0-1)", "default": 0.3},
                    "limit": {"type": "integer", "default": 20},
                },
            }
        ),
        Tool(
            name="get_salience_stats",
            description="Get statistics on salience tagging across memories.",
            inputSchema={
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
            }
        ),
        # ---- Causal tools ----
        Tool(
            name="add_causal_link",
            description="Add a causal relationship between two memories (caused_by, led_to, prevents, enables, requires).",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "Source memory ID"},
                    "target_id": {"type": "string", "description": "Target memory ID"},
                    "relation_type": {
                        "type": "string",
                        "enum": ["caused_by", "led_to", "prevents", "enables", "requires"],
                        "description": "Type of causal relationship",
                    },
                },
                "required": ["source_id", "target_id", "relation_type"],
            }
        ),
        Tool(
            name="get_causal_chain",
            description="Traverse causal links from a memory (what caused it, or what it caused).",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "Starting memory ID"},
                    "direction": {
                        "type": "string",
                        "enum": ["backward", "forward"],
                        "description": "backward=what caused this, forward=what this caused",
                        "default": "backward",
                    },
                    "depth": {"type": "integer", "description": "Max traversal depth", "default": 5},
                },
                "required": ["memory_id"],
            }
        ),
        Tool(
            name="query_causes",
            description="Get both causes and effects for a memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "Memory ID to query"},
                    "depth": {"type": "integer", "default": 3},
                },
                "required": ["memory_id"],
            }
        ),
        # ---- AGI Loop tools ----
        Tool(
            name="get_agi_status",
            description="Get the status of all AGI cognitive subsystems.",
            inputSchema={
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
            }
        ),
        Tool(
            name="run_agi_cycle",
            description="Run one iteration of the full AGI cognitive cycle (consolidate, decay, reconsolidate, etc).",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "context": {"type": "string", "description": "Current context for reconsolidation"},
                },
            }
        ),
        Tool(
            name="get_system_health",
            description="Report health status across all cognitive subsystems.",
            inputSchema={
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
            }
        ),
    ]
    # Append tools from installed power packages
    for name, defn in _get_power_tool_defs().items():
        tools.append(Tool(
            name=name,
            description=defn["description"],
            inputSchema=defn["inputSchema"],
        ))

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


# ---- Salience tools ----

@_tool_handler("tag_salience")
def _handle_tag_salience(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    from engram.core.salience import compute_salience
    memory_id = arguments.get("memory_id", "")
    mem = memory.get(memory_id)
    if not mem:
        return {"error": "Memory not found"}
    content = mem.get("memory", "")
    salience = compute_salience(content, llm=getattr(memory, "llm", None),
                                use_llm=arguments.get("use_llm", False))
    md = mem.get("metadata", {}) or {}
    md.update(salience)
    memory.update(memory_id, {"metadata": md})
    return {"memory_id": memory_id, **salience}


@_tool_handler("search_by_salience")
def _handle_search_by_salience(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    user_id = arguments.get("user_id", "default")
    min_salience = float(arguments.get("min_salience", 0.3))
    try:
        limit = max(1, min(100, int(arguments.get("limit", 20))))
    except (ValueError, TypeError):
        limit = 20
    all_mem = memory.get_all(user_id=user_id, limit=limit * 3)
    items = all_mem.get("results", [])
    results = []
    for m in items:
        md = m.get("metadata", {}) or {}
        score = md.get("sal_salience_score", 0.0)
        if score >= min_salience:
            results.append({
                "id": m["id"],
                "memory": m.get("memory", ""),
                "salience_score": score,
                "valence": md.get("sal_valence", 0.0),
                "arousal": md.get("sal_arousal", 0.0),
            })
    results.sort(key=lambda x: x["salience_score"], reverse=True)
    return {"results": results[:limit], "total": len(results)}


@_tool_handler("get_salience_stats")
def _handle_get_salience_stats(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    user_id = arguments.get("user_id", "default")
    all_mem = memory.get_all(user_id=user_id, limit=500)
    items = all_mem.get("results", [])
    tagged = 0
    total_salience = 0.0
    high_salience = 0
    for m in items:
        md = m.get("metadata", {}) or {}
        score = md.get("sal_salience_score")
        if score is not None:
            tagged += 1
            total_salience += score
            if score >= 0.5:
                high_salience += 1
    return {
        "total_memories": len(items),
        "salience_tagged": tagged,
        "avg_salience": round(total_salience / tagged, 3) if tagged else 0.0,
        "high_salience_count": high_salience,
    }


# ---- Causal tools ----

@_tool_handler("add_causal_link")
def _handle_add_causal_link(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    from engram.core.graph import RelationType
    source_id = arguments.get("source_id", "")
    target_id = arguments.get("target_id", "")
    rel_type = arguments.get("relation_type", "caused_by")
    if not hasattr(memory, "knowledge_graph") or not memory.knowledge_graph:
        return {"error": "Knowledge graph not available"}
    try:
        rt = RelationType(rel_type)
    except ValueError:
        return {"error": f"Invalid relation type: {rel_type}"}
    rel = memory.knowledge_graph.add_relationship(source_id, target_id, rt)
    return rel.to_dict()


@_tool_handler("get_causal_chain")
def _handle_get_causal_chain(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    memory_id = arguments.get("memory_id", "")
    direction = arguments.get("direction", "backward")
    depth = min(10, max(1, int(arguments.get("depth", 5))))
    if not hasattr(memory, "knowledge_graph") or not memory.knowledge_graph:
        return {"error": "Knowledge graph not available"}
    chain = memory.knowledge_graph.get_causal_chain(memory_id, direction, depth)
    return {
        "memory_id": memory_id,
        "direction": direction,
        "chain": [
            {"memory_id": mid, "depth": d, "path": [r.to_dict() for r in path]}
            for mid, d, path in chain
        ],
        "length": len(chain),
    }


@_tool_handler("query_causes")
def _handle_query_causes(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    memory_id = arguments.get("memory_id", "")
    depth = min(10, max(1, int(arguments.get("depth", 3))))
    if not hasattr(memory, "knowledge_graph") or not memory.knowledge_graph:
        return {"error": "Knowledge graph not available"}
    backward = memory.knowledge_graph.get_causal_chain(memory_id, "backward", depth)
    forward = memory.knowledge_graph.get_causal_chain(memory_id, "forward", depth)
    return {
        "memory_id": memory_id,
        "causes": [{"memory_id": mid, "depth": d} for mid, d, _ in backward],
        "effects": [{"memory_id": mid, "depth": d} for mid, d, _ in forward],
    }


# ---- AGI Loop tools ----

@_tool_handler("get_agi_status")
def _handle_get_agi_status(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    from engram.core.agi_loop import get_system_health
    return get_system_health(memory, user_id=arguments.get("user_id", "default"))


@_tool_handler("run_agi_cycle")
def _handle_run_agi_cycle(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    from engram.core.agi_loop import run_agi_cycle
    return run_agi_cycle(
        memory,
        user_id=arguments.get("user_id", "default"),
        context=arguments.get("context"),
    )


@_tool_handler("get_system_health")
def _handle_get_system_health(memory: "Memory", arguments: Dict[str, Any]) -> Any:
    from engram.core.agi_loop import get_system_health
    return get_system_health(memory, user_id=arguments.get("user_id", "default"))


_MEMORY_FREE_TOOLS = {"get_last_session", "save_session_digest"}


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle tool calls."""
    try:
        memory = None if name in _MEMORY_FREE_TOOLS else get_memory()

        handler = _TOOL_HANDLERS.get(name)
        if handler:
            result = handler(memory, arguments)
        elif name in _power_tool_handlers:
            result = _power_tool_handlers[name](arguments)
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
