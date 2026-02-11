"""
engram MCP Server for Claude Code integration.

This server exposes engram's memory capabilities as MCP tools that Claude Code can use.
"""

import atexit
import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from engram.memory.main import Memory
from engram.core.handoff_backend import (
    HandoffBackendError,
    classify_handoff_error,
    create_handoff_backend,
)
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
    # Known embedding model dimensions
    EMBEDDING_DIMS = {
        # Gemini models
        "models/text-embedding-005": 768,
        "text-embedding-005": 768,
        "models/text-embedding-004": 768,
        "text-embedding-004": 768,
        "gemini-embedding-001": 3072,
        # OpenAI models
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    # Check for explicit env var override first
    env_dims = os.environ.get("FADEM_EMBEDDING_DIMS")
    if env_dims:
        return int(env_dims)

    # Look up known dimensions
    if model in EMBEDDING_DIMS:
        return EMBEDDING_DIMS[model]

    # Default based on provider (using latest model defaults)
    if provider == "gemini":
        return 3072  # gemini-embedding-001 default
    elif provider == "openai":
        return 1536
    return 3072


def get_memory_instance() -> Memory:
    """Create and return a configured Memory instance."""
    # Check for API keys in environment
    gemini_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    # Determine LLM and embedder provider based on available keys
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

    # Configure vector store — honour ENGRAM_VECTOR_PROVIDER (default: sqlite_vec)
    vector_provider = os.environ.get("ENGRAM_VECTOR_PROVIDER", "sqlite_vec")
    collection = os.environ.get("FADEM_COLLECTION", "fadem_memories")

    if vector_provider == "sqlite_vec":
        vec_db = os.environ.get(
            "ENGRAM_SQLITE_VEC_PATH",
            os.path.join(os.path.expanduser("~"), ".engram", "vectors.db"),
        )
        vector_store_config = VectorStoreConfig(
            provider="sqlite_vec",
            config={
                "db_path": vec_db,
                "collection_name": collection,
                "embedding_model_dims": embedding_dims,
            },
        )
    else:
        qdrant_path = os.environ.get(
            "FADEM_QDRANT_PATH",
            os.path.join(os.path.expanduser("~"), ".engram", "qdrant"),
        )
        vector_store_config = VectorStoreConfig(
            provider=vector_provider,
            config={
                "path": qdrant_path,
                "collection_name": collection,
                "embedding_model_dims": embedding_dims,
            },
        )

    # Configure history database
    history_db_path = os.environ.get(
        "FADEM_HISTORY_DB",
        os.path.join(os.path.expanduser("~"), ".engram", "history.db")
    )

    # engram-specific settings
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
_handoff_backend = None
_lifecycle_lock = threading.Lock()
_lifecycle_state: Dict[str, Dict[str, Any]] = {}
_idle_pause_seconds = max(1, int(os.environ.get("ENGRAM_MCP_IDLE_PAUSE_SECONDS", "300")))
_shutdown_hooks_registered = False
_shutdown_requested = False


def get_memory() -> Memory:
    """Get or create the global memory instance."""
    global _memory
    if _memory is None:
        with _lifecycle_lock:
            if _memory is None:
                _memory = get_memory_instance()
    return _memory


def _strict_handoff_enabled(memory: Memory) -> bool:
    cfg = getattr(memory, "handoff_config", None)
    return bool(getattr(cfg, "strict_handoff_auth", True))


def get_handoff_backend(memory: Memory):
    """Get or create the configured handoff backend."""
    global _handoff_backend
    if _handoff_backend is None:
        with _lifecycle_lock:
            if _handoff_backend is None:
                _handoff_backend = create_handoff_backend(memory)
    return _handoff_backend


def _handoff_key(*, user_id: str, agent_id: str, namespace: str, repo_id: Optional[str], repo_path: Optional[str]) -> str:
    scoped_repo = str(repo_id or repo_path or "").strip() or "unknown-repo"
    return f"{user_id}::{agent_id}::{namespace}::{scoped_repo}"


def _merge_handoff_context(existing: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing)
    for key, value in update.items():
        if value is not None:
            merged[key] = value
    return merged


_LIFECYCLE_MAX_ENTRIES = 500
_LIFECYCLE_MAX_AGE_SECONDS = 86400  # 24 hours


def _gc_lifecycle_state_locked() -> None:
    """Evict stale runtime entries from _lifecycle_state. Called under _lifecycle_lock.

    NOTE: This only cleans ephemeral in-process handoff context, NOT persistent
    memory data. Actual memories are safely stored in SQLite and vector stores.
    """
    now = time.time()
    expired = [k for k, v in _lifecycle_state.items()
               if now - v.get("last_activity_ts", 0) > _LIFECYCLE_MAX_AGE_SECONDS]
    for k in expired:
        del _lifecycle_state[k]
    if len(_lifecycle_state) > _LIFECYCLE_MAX_ENTRIES:
        sorted_keys = sorted(
            _lifecycle_state,
            key=lambda k: _lifecycle_state[k].get("last_activity_ts", 0),
        )
        for k in sorted_keys[:len(_lifecycle_state) - _LIFECYCLE_MAX_ENTRIES]:
            del _lifecycle_state[k]


def _record_handoff_context(context: Dict[str, Any]) -> None:
    user_id = context.get("user_id", "default")
    agent_id = context.get("agent_id", "claude-code")
    namespace = context.get("namespace", "default")
    repo_path = context.get("repo_path")
    key = _handoff_key(
        user_id=user_id,
        agent_id=agent_id,
        namespace=namespace,
        repo_id=context.get("repo_id"),
        repo_path=repo_path,
    )
    alt_key = _handoff_key(
        user_id=user_id,
        agent_id=agent_id,
        namespace=namespace,
        repo_id=None,
        repo_path=repo_path,
    )
    with _lifecycle_lock:
        now_ts = time.time()
        existing = _lifecycle_state.get(key, {})
        if not existing and alt_key in _lifecycle_state:
            existing = _lifecycle_state.pop(alt_key)
        merged = _merge_handoff_context(existing, context)
        merged["last_activity_ts"] = now_ts
        _lifecycle_state[key] = merged
        # Periodic cleanup to prevent unbounded growth
        if len(_lifecycle_state) > _LIFECYCLE_MAX_ENTRIES:
            _gc_lifecycle_state_locked()


def _emit_lifecycle_checkpoint(memory: Memory, context: Dict[str, Any], *, event_type: str, task_summary: Optional[str]) -> Dict[str, Any]:
    backend = get_handoff_backend(memory)
    payload = {
        "status": "paused" if event_type in {"agent_pause", "agent_end"} else "active",
        "task_summary": task_summary or context.get("objective") or f"{event_type} checkpoint",
        "decisions_made": context.get("decisions_made", []),
        "files_touched": context.get("files_touched", []),
        "todos_remaining": context.get("todos_remaining", []),
        "blockers": context.get("blockers", []),
        "key_commands": context.get("key_commands", []),
        "test_results": context.get("test_results", []),
        "context_snapshot": context.get("context_snapshot"),
    }
    return backend.auto_checkpoint(
        user_id=context["user_id"],
        agent_id=context["agent_id"],
        namespace=context.get("namespace", "default"),
        repo_path=context.get("repo_path") or os.getcwd(),
        branch=context.get("branch"),
        lane_id=context.get("lane_id"),
        lane_type=context.get("lane_type", "general"),
        objective=context.get("objective") or payload["task_summary"],
        agent_role=context.get("agent_role"),
        confidentiality_scope=context.get("confidentiality_scope", "work"),
        payload=payload,
        event_type=event_type,
    )


def _flush_agent_end_checkpoints() -> None:
    """Best-effort final checkpoints on process shutdown."""
    try:
        memory = get_memory()
    except Exception:
        return
    # Use non-blocking acquire so we never deadlock if a signal fired while
    # the lock was already held on the same thread (atexit runs in-process).
    acquired = _lifecycle_lock.acquire(blocking=False)
    try:
        contexts = list(_lifecycle_state.values())
    finally:
        if acquired:
            _lifecycle_lock.release()
    for context in contexts:
        try:
            _emit_lifecycle_checkpoint(
                memory,
                context,
                event_type="agent_end",
                task_summary=context.get("task_summary") or "Agent shutdown",
            )
        except Exception as exc:  # pragma: no cover - best effort shutdown path
            logger.warning("Agent end checkpoint failed: %s", exc)


def _register_shutdown_hooks() -> None:
    global _shutdown_hooks_registered
    if _shutdown_hooks_registered:
        return
    atexit.register(_flush_agent_end_checkpoints)

    def _signal_handler(signum, _frame):  # pragma: no cover - signal path
        # Set a flag instead of acquiring _lifecycle_lock directly to avoid
        # deadlock if the signal fires while _lifecycle_lock is already held.
        global _shutdown_requested
        _shutdown_requested = True
        raise SystemExit(0)

    for sig_name in ("SIGTERM", "SIGINT"):
        sig_value = getattr(signal, sig_name, None)
        if sig_value is not None:
            try:
                signal.signal(sig_value, _signal_handler)
            except Exception:
                logger.debug("Skipping signal hook registration for %s", sig_name)

    _shutdown_hooks_registered = True


# Create the MCP server
server = Server("engram-memory")

# Cached tool list — schemas are static, no need to rebuild on every call.
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
            description="Create a write proposal in staging by default. Supports direct writes only when mode='direct' in trusted local contexts. For simple saves without extras, prefer `remember`.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The memory content to store. Can be a fact, preference, or any important information."
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier to scope this memory to (default: 'default')"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent identifier to scope this memory to (optional)"
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional categories to tag this memory with (e.g., ['preferences', 'personal'])"
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata to attach to the memory"
                    },
                    "agent_category": {
                        "type": "string",
                        "description": "Agent category for scope sharing (e.g. 'coding')"
                    },
                    "connector_id": {
                        "type": "string",
                        "description": "Connector identifier for scope sharing (e.g. 'github')"
                    },
                    "scope": {
                        "type": "string",
                        "description": "Confidentiality scope: work|personal|finance|health|private"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Namespace for scoped memory segmentation (default: 'default')."
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["staging", "direct"],
                        "description": "Write mode. Default staging."
                    },
                    "source_event_id": {
                        "type": "string",
                        "description": "Optional source event ID for provenance."
                    }
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="search_memory",
            description="Search engram for relevant memories by semantic query. The UserPromptSubmit hook handles background search automatically — call this tool only for explicit user recall requests such as 'what did we discuss about X?' or 'recall my preference for Y'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query - what you're trying to remember"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier to search memories for (default: 'default')"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent identifier to scope search to (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 10)"
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter results by categories"
                    },
                    "agent_category": {
                        "type": "string",
                        "description": "Agent category for scope sharing"
                    },
                    "connector_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Connector IDs to include for connector-scope memories"
                    },
                    "scope_filter": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict results to specific scopes"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_all_memories",
            description="Get all stored memories for a user — use for inventory, audit, or when the user wants a full listing. Not for finding specific information; use search_memory for that.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent identifier to scope results to (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of memories to return (default: 50)"
                    },
                    "layer": {
                        "type": "string",
                        "enum": ["sml", "lml"],
                        "description": "Filter by memory layer: 'sml' (short-term) or 'lml' (long-term)"
                    }
                }
            }
        ),
        Tool(
            name="get_memory",
            description="Retrieve a single memory by its ID. Use this only when you already have a memory_id from a prior search or listing. Do not use for discovery — use search_memory instead.",
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
            description="Update an existing memory's content in place. Use when the user corrects or refines something already stored — update rather than duplicating. Requires the memory_id (search first if you don't have it).",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The ID of the memory to update"
                    },
                    "content": {
                        "type": "string",
                        "description": "The new content for the memory"
                    }
                },
                "required": ["memory_id", "content"]
            }
        ),
        Tool(
            name="delete_memory",
            description="Permanently delete a memory by its ID. Only call when the user explicitly asks to forget something. If you don't have the ID, search first and confirm with the user before deleting.",
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
            description="Get statistics about the memory store including counts and layer distribution. Call when the user asks about memory health, wants an overview of what's stored, or runs /engram:status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier to get stats for (default: all users)"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent identifier to scope stats to (optional)"
                    }
                }
            }
        ),
        Tool(
            name="apply_memory_decay",
            description="Apply the memory-decay algorithm to reduce strength of old, unused memories (simulates natural forgetting). Explicit maintenance only — do not call this automatically on every turn.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier to apply decay for (default: all users)"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent identifier to apply decay for (optional)"
                    }
                }
            }
        ),
        Tool(
            name="engram_context",
            description="Session-start digest. Call once at the beginning of a new conversation to load context from prior sessions. Returns top memories sorted by strength with long-term memories (LML) first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier to load context for (default: 'default')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of memories to return in the digest (default: 15)"
                    }
                }
            }
        ),
        Tool(
            name="remember",
            description="Quick-save to staging. Creates a proposal commit with source_app='claude-code' and infer=False by default.",
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
                        "description": "Optional categories to tag this memory with (e.g., ['preferences', 'coding'])"
                    }
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="propose_write",
            description="Create a staging proposal commit for a memory write. Preferred v2 write path for agents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "metadata": {"type": "object"},
                    "scope": {
                        "type": "string",
                        "description": "Confidentiality scope: work|personal|finance|health|private"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Namespace for scoped memory segmentation (default: 'default')."
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["staging", "direct"],
                        "description": "Direct mode reserved for trusted local clients."
                    },
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="list_pending_commits",
            description="List staging commits and their statuses.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "status": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        ),
        Tool(
            name="resolve_conflict",
            description="Resolve a conflict stash entry.",
            inputSchema={
                "type": "object",
                "properties": {
                    "stash_id": {"type": "string"},
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "resolution": {
                        "type": "string",
                        "enum": ["UNRESOLVED", "KEEP_EXISTING", "ACCEPT_PROPOSED", "KEEP_BOTH"],
                    },
                },
                "required": ["stash_id", "resolution"],
            },
        ),
        Tool(
            name="declare_namespace",
            description="Declare a namespace for scoped memory access.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "namespace": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["namespace"],
            },
        ),
        Tool(
            name="grant_namespace_permission",
            description="Grant an agent capability on a namespace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "namespace": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "requester_agent_id": {"type": "string"},
                    "capability": {"type": "string"},
                    "expires_at": {"type": "string"},
                },
                "required": ["namespace", "agent_id"],
            },
        ),
        Tool(
            name="upsert_agent_policy",
            description="Create or update an agent policy used to clamp capability sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "requester_agent_id": {"type": "string"},
                    "allowed_confidentiality_scopes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "allowed_capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "allowed_namespaces": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="list_agent_policies",
            description="List policies for a user, or fetch one policy when agent_id is provided.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "requester_agent_id": {"type": "string"},
                    "include_wildcard": {"type": "boolean"},
                },
            },
        ),
        Tool(
            name="delete_agent_policy",
            description="Delete one policy for a user and agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "requester_agent_id": {"type": "string"},
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="get_agent_trust",
            description="Get trust score stats for an agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "requester_agent_id": {"type": "string"},
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="run_sleep_cycle",
            description="Run the maintenance sleep cycle once (digest + promotion + decay + ref GC).",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "date": {"type": "string"},
                    "apply_decay": {"type": "boolean"},
                    "cleanup_stale_refs": {"type": "boolean"},
                },
            },
        ),
        # ---- Episodic Scene tools ----
        Tool(
            name="get_scene",
            description="Get a specific episodic scene by ID. Returns the scene with its summary, topic, participants, and linked memory IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scene_id": {
                        "type": "string",
                        "description": "The ID of the scene to retrieve"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent identifier for scoped scene reads (optional)",
                    },
                },
                "required": ["scene_id"]
            }
        ),
        Tool(
            name="list_scenes",
            description="List episodic scenes chronologically. Filter by user, topic, or time range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "topic": {
                        "type": "string",
                        "description": "Filter scenes containing this topic keyword"
                    },
                    "start_after": {
                        "type": "string",
                        "description": "Only scenes starting after this ISO timestamp"
                    },
                    "start_before": {
                        "type": "string",
                        "description": "Only scenes starting before this ISO timestamp"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of scenes to return (default: 20)"
                    }
                }
            }
        ),
        Tool(
            name="search_scenes",
            description="Semantic search over episodic scene summaries. Use to find past episodes by topic or content.",
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
                        "description": "Agent identifier for scoped scene search (optional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 10)"
                    }
                },
                "required": ["query"]
            }
        ),
        # ---- Character Profile tools ----
        Tool(
            name="get_profile",
            description="Get a character profile by ID. Returns facts, preferences, relationships, and narrative for a person or entity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile_id": {
                        "type": "string",
                        "description": "The ID of the profile to retrieve"
                    }
                },
                "required": ["profile_id"]
            }
        ),
        Tool(
            name="list_profiles",
            description="List all character profiles for a user. Includes self-profile, contacts, and entities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    }
                }
            }
        ),
        Tool(
            name="search_profiles",
            description="Search character profiles by name or description. Finds people and entities mentioned in memories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (name, fact, or description)"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 10)"
                    }
                },
                "required": ["query"]
            }
        ),
        # ---- Cross-Agent Handoff tools ----
        Tool(
            name="save_session_digest",
            description="Save a session digest before ending or when interrupted. Enables cross-agent handoff so another agent can continue where you left off.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_summary": {
                        "type": "string",
                        "description": "What was the agent doing — the main task being worked on"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Identifier of the agent saving the digest (default: 'claude-code')"
                    },
                    "requester_agent_id": {
                        "type": "string",
                        "description": "Agent identity performing this write (defaults to agent_id)."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository or project path for scoping"
                    },
                    "branch": {
                        "type": "string",
                        "description": "Optional branch name for lane routing"
                    },
                    "lane_id": {
                        "type": "string",
                        "description": "Optional lane identifier for checkpointing"
                    },
                    "lane_type": {
                        "type": "string",
                        "description": "Lane category (default: general)"
                    },
                    "agent_role": {
                        "type": "string",
                        "description": "Role of source agent (pm/design/frontend/backend/etc.)"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Namespace scope for handoff (default: default)"
                    },
                    "confidentiality_scope": {
                        "type": "string",
                        "description": "Confidentiality scope for handoff (default: work)"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "paused", "completed", "abandoned"],
                        "description": "Session status (default: 'paused')"
                    },
                    "decisions_made": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Key decisions made during the session"
                    },
                    "files_touched": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File paths modified during the session"
                    },
                    "todos_remaining": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Remaining work items for the next agent"
                    },
                    "blockers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Known blockers for the receiving agent"
                    },
                    "key_commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Important commands run during the session"
                    },
                    "test_results": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Recent test outcomes"
                    },
                    "context_snapshot": {
                        "type": "string",
                        "description": "Free-form context blob for the receiving agent"
                    },
                    "started_at": {
                        "type": "string",
                        "description": "ISO timestamp when the session started"
                    },
                    "ended_at": {
                        "type": "string",
                        "description": "ISO timestamp when the session ended"
                    },
                },
                "required": ["task_summary"]
            }
        ),
        Tool(
            name="get_last_session",
            description="Get the most recent session digest to continue where the last agent left off. Returns full handoff context including linked memories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Filter by source agent identifier"
                    },
                    "requester_agent_id": {
                        "type": "string",
                        "description": "Agent identity performing this read."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Filter by repository/project path"
                    },
                    "statuses": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["active", "paused", "completed", "abandoned"],
                        },
                        "description": "Optional status list filter (defaults to active/paused)"
                    },
                }
            }
        ),
        Tool(
            name="list_sessions",
            description="Browse session handoff history. Returns a summary list of past sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Filter by agent identifier"
                    },
                    "requester_agent_id": {
                        "type": "string",
                        "description": "Agent identity performing this read."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Filter by repository/project path"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "paused", "completed", "abandoned"],
                        "description": "Filter by session status"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sessions to return (default: 20)"
                    },
                    "statuses": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["active", "paused", "completed", "abandoned"],
                        },
                        "description": "Optional status list filter."
                    },
                }
            }
        ),
        # ---- Active Memory (signal bus) tools ----
        Tool(
            name="signal_write",
            description="Post a signal to the active memory bus. State signals upsert by key+agent; events always create new; directives are permanent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Signal key (e.g., 'editing_file', 'build_status', 'use_typescript')"
                    },
                    "value": {
                        "type": "string",
                        "description": "Signal value/content"
                    },
                    "signal_type": {
                        "type": "string",
                        "enum": ["state", "event", "directive"],
                        "description": "Signal type: state (current status, upserts), event (one-shot), directive (permanent rule). Default: state"
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "repo", "namespace"],
                        "description": "Visibility scope. Default: global"
                    },
                    "scope_key": {
                        "type": "string",
                        "description": "Scope qualifier (e.g., repo path for scope=repo)"
                    },
                    "ttl_tier": {
                        "type": "string",
                        "enum": ["noise", "notable", "critical", "directive"],
                        "description": "TTL tier: noise (30m), notable (2h), critical (24h), directive (permanent). Default: notable"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Source agent identifier"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                },
                "required": ["key", "value"],
            }
        ),
        Tool(
            name="signal_read",
            description="Read active signals from the memory bus. Returns signals ordered by priority (directive > critical > notable > noise).",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["global", "repo", "namespace"],
                        "description": "Filter by visibility scope"
                    },
                    "scope_key": {
                        "type": "string",
                        "description": "Filter by scope qualifier"
                    },
                    "signal_type": {
                        "type": "string",
                        "enum": ["state", "event", "directive"],
                        "description": "Filter by signal type"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Reader agent identifier (tracked for read_by)"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum signals to return"
                    },
                },
            }
        ),
        Tool(
            name="signal_clear",
            description="Clear signals from the active memory bus matching the given criteria.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Clear signals with this key"
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "repo", "namespace"],
                        "description": "Clear signals with this scope"
                    },
                    "scope_key": {
                        "type": "string",
                        "description": "Clear signals with this scope key"
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Clear signals from this agent"
                    },
                    "signal_type": {
                        "type": "string",
                        "enum": ["state", "event", "directive"],
                        "description": "Clear signals of this type"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User identifier (default: 'default')"
                    },
                },
            }
        ),
    ]

    # Some MCP clients cap/trim tool manifests per chat.
    # Keep handoff tools at the front so cross-agent continuity remains available.
    priority = {"save_session_digest": 0, "get_last_session": 1, "list_sessions": 2}
    tools.sort(key=lambda tool: priority.get(tool.name, 1000))
    _tools_cache = tools
    return list(tools)


# Phase 6: Tool handler registry for cleaner dispatch.
_TOOL_HANDLERS: Dict[str, Callable] = {}


def _preview(value: Any, limit: int = 1200) -> str:
    """Truncate a JSON-serialized value for checkpoint snapshots."""
    try:
        text = json.dumps(value, default=str)
    except Exception:
        text = str(value)
    if len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


def _make_session_token(memory: "Memory", *, user_id: str, agent_id: Optional[str], capabilities: List[str], namespaces: Optional[List[str]] = None) -> str:
    """Create a scoped session token."""
    session = memory.create_session(
        user_id=user_id,
        agent_id=agent_id,
        allowed_confidentiality_scopes=["work", "personal", "finance", "health", "private"],
        capabilities=capabilities,
        namespaces=namespaces,
        ttl_minutes=24 * 60,
    )
    return session["token"]


def _tool_handler(name: str):
    """Decorator to register a tool handler function."""
    def decorator(fn):
        _TOOL_HANDLERS[name] = fn
        return fn
    return decorator


@_tool_handler("get_memory")
def _handle_get_memory(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    memory_id = arguments.get("memory_id", "")
    result = memory.get(memory_id)
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
def _handle_update_memory(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    memory_id = arguments.get("memory_id", "")
    content = arguments.get("content", "")
    return memory.update(memory_id, content)


@_tool_handler("delete_memory")
def _handle_delete_memory(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    memory_id = arguments.get("memory_id", "")
    return memory.delete(memory_id)


@_tool_handler("get_memory_stats")
def _handle_get_memory_stats(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id")
    agent_id = arguments.get("agent_id")
    return memory.get_stats(user_id=user_id, agent_id=agent_id)


@_tool_handler("apply_memory_decay")
def _handle_apply_memory_decay(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id")
    agent_id = arguments.get("agent_id")
    scope = {"user_id": user_id, "agent_id": agent_id} if user_id or agent_id else None
    return memory.apply_decay(scope=scope)


@_tool_handler("engram_context")
def _handle_engram_context(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
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
    return {"digest": digest, "total_in_store": len(all_memories), "returned": len(digest)}


@_tool_handler("get_profile")
def _handle_get_profile(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    profile_id = arguments.get("profile_id", "")
    profile = memory.get_profile(profile_id)
    if profile:
        profile.pop("embedding", None)
        return profile
    return {"error": "Profile not found"}


@_tool_handler("list_profiles")
def _handle_list_profiles(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    profiles = memory.get_all_profiles(user_id=user_id)
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
def _handle_search_profiles(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    query = arguments.get("query", "")
    user_id = arguments.get("user_id", "default")
    try:
        limit = max(1, min(100, int(arguments.get("limit", 10))))
    except (ValueError, TypeError):
        limit = 10
    profiles = memory.search_profiles(query=query, user_id=user_id, limit=limit)
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


@_tool_handler("add_memory")
@_tool_handler("propose_write")
def _handle_add_memory(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    content = arguments.get("content", "")
    user_id = arguments.get("user_id", "default")
    agent_id = arguments.get("agent_id")
    namespace = arguments.get("namespace", "default")
    token = _session_token(
        user_id=user_id,
        agent_id=agent_id,
        capabilities=["propose_write"],
        namespaces=[namespace],
    )
    return memory.propose_write(
        content=content,
        user_id=user_id,
        agent_id=agent_id,
        categories=arguments.get("categories"),
        metadata=arguments.get("metadata"),
        scope=arguments.get("scope", "work"),
        namespace=namespace,
        mode=arguments.get("mode", "staging"),
        infer=False,
        token=token,
        source_app="mcp",
        source_type="mcp",
        source_event_id=arguments.get("source_event_id"),
    )


@_tool_handler("search_memory")
def _handle_search_memory(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    query = arguments.get("query", "")
    user_id = arguments.get("user_id", "default")
    agent_id = arguments.get("agent_id")
    try:
        limit = max(1, min(1000, int(arguments.get("limit", 10))))
    except (ValueError, TypeError):
        limit = 10
    categories = arguments.get("categories")
    if agent_id:
        token = _session_token(user_id=user_id, agent_id=agent_id, capabilities=["search"])
        result = memory.search_with_context(
            query=query, user_id=user_id, agent_id=agent_id, token=token, limit=limit, categories=categories,
        )
    else:
        result = memory.search(
            query=query, user_id=user_id, agent_id=agent_id, limit=limit, categories=categories,
            agent_category=arguments.get("agent_category"),
            connector_ids=arguments.get("connector_ids"),
            scope_filter=arguments.get("scope_filter"),
        )
    if "results" in result:
        result["results"] = [
            {
                "id": r.get("id"),
                "memory": r.get("memory", r.get("details", "")),
                "score": round(r.get("composite_score", r.get("score", 0)), 3),
                "layer": r.get("layer", "sml"),
                "categories": r.get("categories", []),
                "scope": r.get("scope"),
                "agent_category": r.get("agent_category"),
                "connector_id": r.get("connector_id"),
            }
            for r in result["results"]
        ]
    return result


@_tool_handler("get_all_memories")
def _handle_get_all_memories(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
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


@_tool_handler("remember")
def _handle_remember(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    namespace = arguments.get("namespace", "default")
    token = _session_token(
        user_id="default",
        agent_id="claude-code",
        capabilities=["propose_write"],
        namespaces=[namespace],
    )
    return memory.propose_write(
        content=arguments.get("content", ""),
        user_id="default",
        agent_id="claude-code",
        categories=arguments.get("categories"),
        scope=arguments.get("scope", "work"),
        namespace=namespace,
        mode=arguments.get("mode", "staging"),
        source_app="claude-code",
        source_type="mcp",
        infer=False,
        token=token,
    )


@_tool_handler("list_pending_commits")
def _handle_list_pending_commits(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    agent_id = arguments.get("agent_id", "claude-code")
    token = _session_token(user_id=user_id, agent_id=agent_id, capabilities=["review_commits"])
    return memory.list_pending_commits(
        user_id=user_id, agent_id=agent_id, token=token,
        status=arguments.get("status"),
        limit=max(1, min(1000, int(arguments.get("limit", 100)))) if arguments.get("limit") is not None else 100,
    )


@_tool_handler("resolve_conflict")
def _handle_resolve_conflict(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    agent_id = arguments.get("agent_id", "claude-code")
    token = _session_token(
        user_id=arguments.get("user_id", "default"),
        agent_id=agent_id,
        capabilities=["resolve_conflicts"],
    )
    return memory.resolve_conflict(
        stash_id=arguments.get("stash_id", ""),
        resolution=arguments.get("resolution", "UNRESOLVED"),
        token=token, agent_id=agent_id,
    )


@_tool_handler("declare_namespace")
def _handle_declare_namespace(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    caller_agent_id = arguments.get("agent_id", "claude-code")
    namespace = arguments.get("namespace", "default")
    token = _session_token(
        user_id=user_id, agent_id=caller_agent_id,
        capabilities=["manage_namespaces"], namespaces=[namespace],
    )
    return memory.declare_namespace(
        user_id=user_id, namespace=namespace,
        description=arguments.get("description"), token=token, agent_id=caller_agent_id,
    )


@_tool_handler("grant_namespace_permission")
def _handle_grant_namespace_permission(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    requester_agent_id = arguments.get("requester_agent_id", arguments.get("agent_id", "claude-code"))
    namespace = arguments.get("namespace", "default")
    token = _session_token(
        user_id=user_id, agent_id=requester_agent_id,
        capabilities=["manage_namespaces"], namespaces=[namespace],
    )
    return memory.grant_namespace_permission(
        user_id=user_id, namespace=namespace,
        agent_id=arguments.get("agent_id", "claude-code"),
        capability=arguments.get("capability", "read"),
        expires_at=arguments.get("expires_at"),
        token=token, requester_agent_id=requester_agent_id,
    )


@_tool_handler("upsert_agent_policy")
def _handle_upsert_agent_policy(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    requester_agent_id = arguments.get("requester_agent_id", arguments.get("agent_id", "claude-code"))
    token = _session_token(user_id=user_id, agent_id=requester_agent_id, capabilities=["manage_namespaces"])
    return memory.upsert_agent_policy(
        user_id=user_id,
        agent_id=arguments.get("agent_id", "claude-code"),
        allowed_confidentiality_scopes=arguments.get("allowed_confidentiality_scopes"),
        allowed_capabilities=arguments.get("allowed_capabilities"),
        allowed_namespaces=arguments.get("allowed_namespaces"),
        token=token, requester_agent_id=requester_agent_id,
    )


@_tool_handler("list_agent_policies")
def _handle_list_agent_policies(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    requester_agent_id = arguments.get("requester_agent_id", arguments.get("agent_id", "claude-code"))
    token = _session_token(user_id=user_id, agent_id=requester_agent_id, capabilities=["manage_namespaces"])
    lookup_agent_id = arguments.get("agent_id")
    if lookup_agent_id:
        return memory.get_agent_policy(
            user_id=user_id, agent_id=lookup_agent_id,
            include_wildcard=arguments.get("include_wildcard", True),
            token=token, requester_agent_id=requester_agent_id,
        )
    return memory.list_agent_policies(
        user_id=user_id, token=token, requester_agent_id=requester_agent_id,
    )


@_tool_handler("delete_agent_policy")
def _handle_delete_agent_policy(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    requester_agent_id = arguments.get("requester_agent_id", arguments.get("agent_id", "claude-code"))
    token = _session_token(user_id=user_id, agent_id=requester_agent_id, capabilities=["manage_namespaces"])
    return memory.delete_agent_policy(
        user_id=user_id, agent_id=arguments.get("agent_id", "claude-code"),
        token=token, requester_agent_id=requester_agent_id,
    )


@_tool_handler("get_agent_trust")
def _handle_get_agent_trust(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    requester_agent_id = arguments.get("requester_agent_id", arguments.get("agent_id", "claude-code"))
    token = _session_token(user_id=user_id, agent_id=requester_agent_id, capabilities=["read_trust"])
    return memory.get_agent_trust(
        user_id=user_id, agent_id=arguments.get("agent_id", "claude-code"),
        token=token, requester_agent_id=requester_agent_id,
    )


@_tool_handler("run_sleep_cycle")
def _handle_run_sleep_cycle(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    caller_agent_id = arguments.get("agent_id", "claude-code")
    token = _session_token(user_id=user_id, agent_id=caller_agent_id, capabilities=["run_sleep_cycle"])
    return memory.run_sleep_cycle(
        user_id=arguments.get("user_id"),
        date_str=arguments.get("date"),
        apply_decay=arguments.get("apply_decay", True),
        cleanup_stale_refs=arguments.get("cleanup_stale_refs", True),
        token=token, agent_id=caller_agent_id,
    )


@_tool_handler("get_scene")
def _handle_get_scene(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    agent_id = arguments.get("agent_id", "claude-code")
    token = _session_token(user_id=user_id, agent_id=agent_id, capabilities=["read_scene"])
    scene = memory.kernel.get_scene(
        scene_id=arguments.get("scene_id", ""),
        user_id=user_id, agent_id=agent_id, token=token,
    )
    return scene if scene else {"error": "Scene not found"}


@_tool_handler("list_scenes")
def _handle_list_scenes(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
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
def _handle_search_scenes(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    user_id = arguments.get("user_id", "default")
    agent_id = arguments.get("agent_id", "claude-code")
    token = _session_token(user_id=user_id, agent_id=agent_id, capabilities=["read_scene"])
    try:
        scene_search_limit = max(1, min(100, int(arguments.get("limit", 10))))
    except (ValueError, TypeError):
        scene_search_limit = 10
    payload = memory.kernel.search_scenes(
        query=arguments.get("query", ""),
        user_id=user_id, agent_id=agent_id, token=token,
        limit=scene_search_limit,
    )
    scenes = payload.get("scenes", [])
    return {
        "scenes": [
            {
                "id": s.get("id"),
                "title": s.get("title"),
                "summary": s.get("summary", s.get("details")),
                "topic": s.get("topic"),
                "start_time": s.get("start_time", s.get("time")),
                "search_score": s.get("search_score"),
                "memory_count": len(s.get("memory_ids", [])),
                "masked": bool(s.get("masked", False)),
            }
            for s in scenes
        ],
        "total": len(scenes),
    }


# ---- Active Memory (signal bus) helpers and handlers ----

_active_store = None
_active_store_lock = threading.Lock()


def _get_active_store(memory: Memory):
    """Lazy-initialize the global active memory store."""
    global _active_store
    if _active_store is None:
        with _active_store_lock:
            if _active_store is None:
                if memory.config.active.enabled:
                    from engram.core.active_memory import ActiveMemoryStore
                    _active_store = ActiveMemoryStore(memory.config.active)
    return _active_store


@_tool_handler("signal_write")
def _handle_signal_write(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    # Validate required fields
    key = arguments.get("key")
    if key is None or not isinstance(key, str) or not key.strip():
        return {"error": "signal_write requires 'key' parameter"}
    value = arguments.get("value")
    if value is None or not isinstance(value, str):
        return {"error": "signal_write requires 'value' parameter (string)"}

    # Validate signal_type enum
    _VALID_SIGNAL_TYPES = {"state", "event", "directive"}
    signal_type = arguments.get("signal_type", "state")
    if signal_type not in _VALID_SIGNAL_TYPES:
        return {"error": f"signal_write 'signal_type' must be one of {sorted(_VALID_SIGNAL_TYPES)}, got '{signal_type}'"}

    # Validate ttl_tier enum
    _VALID_TTL_TIERS = {"noise", "notable", "critical", "directive"}
    ttl_tier = arguments.get("ttl_tier", "notable")
    if ttl_tier not in _VALID_TTL_TIERS:
        return {"error": f"signal_write 'ttl_tier' must be one of {sorted(_VALID_TTL_TIERS)}, got '{ttl_tier}'"}

    active = _get_active_store(memory)
    if not active:
        return {"error": "Active memory is disabled"}
    return active.write_signal(
        key=key,
        value=value,
        signal_type=signal_type,
        scope=arguments.get("scope", "global"),
        scope_key=arguments.get("scope_key"),
        ttl_tier=ttl_tier,
        source_agent_id=arguments.get("agent_id"),
        user_id=arguments.get("user_id", "default"),
    )


@_tool_handler("signal_read")
def _handle_signal_read(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    active = _get_active_store(memory)
    if not active:
        return {"error": "Active memory is disabled"}
    raw_limit = arguments.get("limit")
    if raw_limit is not None:
        try:
            limit = max(1, min(1000, int(raw_limit)))
        except (ValueError, TypeError):
            limit = None
    else:
        limit = None
    return active.read_signals(
        scope=arguments.get("scope"),
        scope_key=arguments.get("scope_key"),
        signal_type=arguments.get("signal_type"),
        user_id=arguments.get("user_id", "default"),
        reader_agent_id=arguments.get("agent_id"),
        limit=limit,
    )


@_tool_handler("signal_clear")
def _handle_signal_clear(memory: "Memory", arguments: Dict[str, Any], _session_token, _preview) -> Any:
    # Validate signal_type enum if provided
    _VALID_SIGNAL_TYPES = {"state", "event", "directive"}
    signal_type = arguments.get("signal_type")
    if signal_type is not None and signal_type not in _VALID_SIGNAL_TYPES:
        return {"error": f"signal_clear 'signal_type' must be one of {sorted(_VALID_SIGNAL_TYPES)}, got '{signal_type}'"}

    active = _get_active_store(memory)
    if not active:
        return {"error": "Active memory is disabled"}
    return active.clear_signals(
        key=arguments.get("key"),
        scope=arguments.get("scope"),
        scope_key=arguments.get("scope_key"),
        source_agent_id=arguments.get("agent_id"),
        signal_type=signal_type,
        user_id=arguments.get("user_id", "default"),
    )


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle tool calls."""
    try:
        memory = get_memory()
        result: Any = None

        def _session_token(*, user_id: str, agent_id: Optional[str], capabilities: List[str], namespaces: Optional[List[str]] = None) -> str:
            return _make_session_token(memory, user_id=user_id, agent_id=agent_id, capabilities=capabilities, namespaces=namespaces)

        def _handoff_error_payload(exc: Exception) -> Dict[str, str]:
            if isinstance(exc, HandoffBackendError):
                return exc.to_dict()
            return classify_handoff_error(exc).to_dict()

        auto_handoff_enabled = bool(
            getattr(memory, "handoff_processor", None)
            and getattr(memory, "handoff_config", None)
            and getattr(memory.handoff_config, "auto_session_bus", False)
        )
        auto_handoff_skip_tools = {"save_session_digest", "get_last_session", "list_sessions"}
        auto_handoff_context: Dict[str, Any] = {}
        auto_handoff_meta: Dict[str, Any] = {}
        auto_resume_packet: Optional[Dict[str, Any]] = None
        handoff_backend = None

        if auto_handoff_enabled and name not in auto_handoff_skip_tools:
            caller_agent_id = (
                arguments.get("requester_agent_id")
                or arguments.get("agent_id")
                or os.environ.get("ENGRAM_MCP_AGENT_ID")
                or "claude-code"
            )
            user_id = arguments.get("user_id", "default")
            namespace = arguments.get("namespace", "default")
            repo_path = arguments.get("repo") or arguments.get("repo_path") or os.getcwd()
            objective = (
                arguments.get("task_summary")
                or arguments.get("objective")
                or arguments.get("query")
                or f"{name} task"
            )
            auto_handoff_context = {
                "user_id": user_id,
                "agent_id": caller_agent_id,
                "namespace": namespace,
                "repo_path": repo_path,
                "branch": arguments.get("branch"),
                "lane_id": arguments.get("lane_id"),
                "lane_type": arguments.get("lane_type", "general"),
                "objective": objective,
                "agent_role": arguments.get("agent_role"),
                "confidentiality_scope": arguments.get("confidentiality_scope", "work"),
            }
            try:
                handoff_backend = get_handoff_backend(memory)
            except Exception as backend_exc:
                auto_handoff_meta["error"] = _handoff_error_payload(backend_exc)
                handoff_backend = None

            if handoff_backend is not None:
                auto_handoff_key = _handoff_key(
                    user_id=user_id,
                    agent_id=caller_agent_id,
                    namespace=namespace,
                    repo_id=None,
                    repo_path=repo_path,
                )
                now_ts = time.time()
                with _lifecycle_lock:
                    previous_context = dict(_lifecycle_state.get(auto_handoff_key, {}))
                last_activity_ts = float(previous_context.get("last_activity_ts", now_ts))
                idle_for_seconds = max(0.0, now_ts - last_activity_ts)
                if (
                    previous_context
                    and idle_for_seconds >= _idle_pause_seconds
                    and "agent_pause" in getattr(memory.handoff_config, "auto_checkpoint_events", [])
                ):
                    try:
                        pause_result = _emit_lifecycle_checkpoint(
                            memory,
                            previous_context,
                            event_type="agent_pause",
                            task_summary=previous_context.get("task_summary") or f"Idle pause before {name}",
                        )
                        auto_handoff_meta["pause"] = pause_result
                    except Exception as pause_exc:
                        auto_handoff_meta["pause"] = {"error": _handoff_error_payload(pause_exc)}

                try:
                    auto_resume_packet = handoff_backend.auto_resume_context(
                        user_id=user_id,
                        agent_id=caller_agent_id,
                        namespace=namespace,
                        repo_path=repo_path,
                        branch=arguments.get("branch"),
                        lane_type=arguments.get("lane_type", "general"),
                        objective=objective,
                        agent_role=arguments.get("agent_role"),
                    )
                    if auto_resume_packet:
                        auto_handoff_context["lane_id"] = auto_resume_packet.get("lane_id") or auto_handoff_context["lane_id"]
                        auto_handoff_context["repo_id"] = auto_resume_packet.get("repo_id")
                        auto_handoff_context["task_summary"] = auto_resume_packet.get("task_summary")
                except Exception as resume_exc:
                    auto_handoff_meta["error"] = _handoff_error_payload(resume_exc)
                    auto_resume_packet = None

            if handoff_backend is None and _strict_handoff_enabled(memory):
                auto_handoff_meta.setdefault(
                    "error",
                    {"code": "hosted_backend_unavailable", "message": "Handoff backend is unavailable"},
                )

            if handoff_backend is not None and auto_resume_packet is None and "error" not in auto_handoff_meta:
                auto_handoff_meta["error"] = {
                    "code": "lane_resolution_failed",
                    "message": "Unable to build resume context",
                }
        elif auto_handoff_enabled:
            try:
                handoff_backend = get_handoff_backend(memory)
            except Exception:
                handoff_backend = None

        if auto_handoff_context:
            _record_handoff_context(auto_handoff_context)

        # Tool dispatch: registry handles all tools except handoff tools
        # (which need access to the local handoff_backend variable).
        handler = _TOOL_HANDLERS.get(name)
        if handler:
            result = handler(memory, arguments, _session_token, _preview)

        # ---- Handoff tools (need local handoff_backend) ----
        elif name == "save_session_digest":
            user_id = arguments.get("user_id", "default")
            agent_id = arguments.get("agent_id", "claude-code")
            requester_agent_id = arguments.get("requester_agent_id", agent_id)
            namespace = arguments.get("namespace", "default")
            task_summary = str(arguments.get("task_summary", "")).strip()
            if not task_summary:
                result = {"error": "task_summary is required"}
            else:
                digest = {
                    "task_summary": task_summary,
                    "repo": arguments.get("repo"),
                    "branch": arguments.get("branch"),
                    "lane_id": arguments.get("lane_id"),
                    "lane_type": arguments.get("lane_type"),
                    "agent_role": arguments.get("agent_role"),
                    "namespace": namespace,
                    "confidentiality_scope": arguments.get("confidentiality_scope", "work"),
                    "status": arguments.get("status", "paused"),
                    "decisions_made": arguments.get("decisions_made", []),
                    "files_touched": arguments.get("files_touched", []),
                    "todos_remaining": arguments.get("todos_remaining", []),
                    "blockers": arguments.get("blockers", []),
                    "key_commands": arguments.get("key_commands", []),
                    "test_results": arguments.get("test_results", []),
                    "context_snapshot": arguments.get("context_snapshot"),
                    "started_at": arguments.get("started_at"),
                    "ended_at": arguments.get("ended_at"),
                }
                try:
                    handoff_backend = handoff_backend or get_handoff_backend(memory)
                    result = handoff_backend.save_session_digest(
                        user_id=user_id,
                        agent_id=agent_id,
                        requester_agent_id=requester_agent_id,
                        namespace=namespace,
                        digest=digest,
                    )
                except Exception as handoff_exc:
                    error_payload = _handoff_error_payload(handoff_exc)
                    result = {"error": error_payload["message"], "_handoff": {"error": error_payload}}

        elif name == "get_last_session":
            user_id = arguments.get("user_id", "default")
            agent_id = arguments.get("agent_id")
            requester_agent_id = arguments.get(
                "requester_agent_id",
                arguments.get("agent_id", "claude-code"),
            )
            namespace = arguments.get("namespace", "default")
            repo = arguments.get("repo")
            try:
                handoff_backend = handoff_backend or get_handoff_backend(memory)
                session = handoff_backend.get_last_session(
                    user_id=user_id,
                    agent_id=agent_id,
                    requester_agent_id=requester_agent_id,
                    namespace=namespace,
                    repo=repo,
                    statuses=arguments.get("statuses"),
                )
                result = session if session else {"error": "No sessions found"}
            except Exception as handoff_exc:
                error_payload = _handoff_error_payload(handoff_exc)
                result = {"error": error_payload["message"], "_handoff": {"error": error_payload}}

        elif name == "list_sessions":
            user_id = arguments.get("user_id", "default")
            requester_agent_id = arguments.get(
                "requester_agent_id",
                arguments.get("agent_id", "claude-code"),
            )
            namespace = arguments.get("namespace", "default")
            try:
                handoff_backend = handoff_backend or get_handoff_backend(memory)
                sessions = handoff_backend.list_sessions(
                    user_id=user_id,
                    agent_id=arguments.get("agent_id"),
                    requester_agent_id=requester_agent_id,
                    namespace=namespace,
                    repo=arguments.get("repo"),
                    status=arguments.get("status"),
                    statuses=arguments.get("statuses"),
                    limit=max(1, min(200, int(arguments.get("limit", 20)))) if arguments.get("limit") is not None else 20,
                )
                result = {
                    "sessions": [
                        {
                            "id": s["id"],
                            "agent_id": s.get("agent_id"),
                            "repo": s.get("repo"),
                            "repo_id": s.get("repo_id"),
                            "lane_id": s.get("lane_id"),
                            "status": s.get("status"),
                            "task_summary": s.get("task_summary", "")[:200],
                            "last_checkpoint_at": s.get("last_checkpoint_at"),
                            "updated_at": s.get("updated_at"),
                        }
                        for s in sessions
                    ],
                    "total": len(sessions),
                }
            except Exception as handoff_exc:
                error_payload = _handoff_error_payload(handoff_exc)
                result = {"error": error_payload["message"], "_handoff": {"error": error_payload}}

        else:
            result = {"error": f"Unknown tool: {name}"}

        if (
            auto_handoff_enabled
            and name not in auto_handoff_skip_tools
            and auto_handoff_context
            and "tool_complete" in getattr(memory.handoff_config, "auto_checkpoint_events", [])
        ):
            checkpoint_payload = {
                "status": "active",
                "task_summary": (
                    arguments.get("task_summary")
                    or arguments.get("objective")
                    or arguments.get("query")
                    or f"{name} completed"
                ),
                "decisions_made": arguments.get("decisions_made", []),
                "files_touched": arguments.get("files_touched", []),
                "todos_remaining": arguments.get("todos_remaining", []),
                "blockers": arguments.get("blockers", []),
                "key_commands": arguments.get("key_commands", []),
                "test_results": arguments.get("test_results", []),
                "context_snapshot": _preview(
                    {
                        "tool": name,
                        "arguments": arguments,
                        "result": result,
                    },
                    limit=2000,
                ),
            }
            try:
                handoff_backend = handoff_backend or get_handoff_backend(memory)
                checkpoint_result = handoff_backend.auto_checkpoint(
                    user_id=auto_handoff_context["user_id"],
                    agent_id=auto_handoff_context["agent_id"],
                    namespace=auto_handoff_context["namespace"],
                    repo_path=auto_handoff_context["repo_path"],
                    branch=auto_handoff_context["branch"],
                    lane_id=auto_handoff_context.get("lane_id"),
                    lane_type=auto_handoff_context["lane_type"],
                    objective=auto_handoff_context["objective"],
                    agent_role=auto_handoff_context["agent_role"],
                    confidentiality_scope=auto_handoff_context["confidentiality_scope"],
                    payload=checkpoint_payload,
                    event_type="tool_complete",
                )
                if isinstance(checkpoint_result, dict) and checkpoint_result.get("lane_id"):
                    auto_handoff_context["lane_id"] = checkpoint_result["lane_id"]
                auto_handoff_context["task_summary"] = checkpoint_payload["task_summary"]
                auto_handoff_context["context_snapshot"] = checkpoint_payload["context_snapshot"]
                _record_handoff_context(auto_handoff_context)
            except Exception as checkpoint_exc:
                checkpoint_result = {"error": _handoff_error_payload(checkpoint_exc)}

            if isinstance(result, dict):
                handoff_meta: Dict[str, Any] = {"checkpoint": checkpoint_result}
                if auto_handoff_meta:
                    handoff_meta.update(auto_handoff_meta)
                if auto_resume_packet:
                    handoff_meta["resume"] = auto_resume_packet
                result["_handoff"] = handoff_meta

        if isinstance(result, dict) and auto_handoff_meta and "_handoff" not in result:
            handoff_meta = dict(auto_handoff_meta)
            if auto_resume_packet:
                handoff_meta["resume"] = auto_resume_packet
            result["_handoff"] = handoff_meta

        # Active memory auto-injection: attach latest signals to every response.
        # Use peek_signals (read-only) to avoid inflating read_count on every
        # tool call; only explicit signal_read calls should bump the counter.
        if isinstance(result, dict):
            active_store = _get_active_store(memory)
            if active_store:
                try:
                    signals = active_store.peek_signals(
                        user_id=arguments.get("user_id", "default"),
                        limit=memory.config.active.max_signals_per_response,
                    )
                    if signals:
                        result["_active"] = signals
                except Exception as active_err:
                    logger.debug("Active memory injection failed: %s", active_err)

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as e:
        logger.exception("MCP tool '%s' failed", name)
        # Sanitize error — only expose the exception class name + message, not internals
        error_msg = f"{type(e).__name__}: {e}"
        error_result = {"error": error_msg}
        return [TextContent(type="text", text=json.dumps(error_result, indent=2))]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run():
    """Entry point for the MCP server."""
    import asyncio
    _register_shutdown_hooks()
    asyncio.run(main())


if __name__ == "__main__":
    run()
