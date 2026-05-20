"""Dhee MCP Server — artifact-aware context-memory tools, minimal boilerplate.

Tools:
 1. remember             — Quick-save (content → memory, infer=False)
 2. search_memory        — Semantic search
 3. get_memory           — Fetch by ID
 4. get_all_memories     — List with filters
 5. dhee_context         — HyperAgent session bootstrap (Buddhi-powered)
 6. get_last_session     — Handoff: load prior session
 7. save_session_digest  — Handoff: save current session
 8. get_memory_stats     — Quick health check
 9. audit_memory_quality — Read-only personal-memory readiness audit
10. search_skills        — Semantic search over skills
11. apply_skill          — Inject skill recipe into context
12. log_skill_outcome    — Report success/failure for a skill
13. record_trajectory_step — Record a step in active trajectory
14. mine_skills          — Run skill mining cycle
15. get_skill_stats      — Statistics about skills and trajectories
16. search_skills_structural — Find skills by structural similarity
17. analyze_skill_gaps   — Show what transfers vs what needs experimentation
18. decompose_skill      — Trigger structural decomposition of a flat skill
19. apply_skill_with_bindings — Apply skill with slot values, includes gap analysis
20. enrich_pending       — Batch-enrich deferred memories
21. think                — Cognitive decomposition (memory-grounded reasoning)
22. anticipate           — Proactive scene + intention surfacing (Buddhi-powered)
23. record_outcome       — Report task outcome for performance tracking
24. reflect              — Agent-triggered insight synthesis
25. store_intention      — Store a future trigger (prospective memory)
26. dhee_list_assets     — List stored host-parsed artifacts
27. dhee_get_asset       — Inspect a stored artifact and its bindings/chunks
28. dhee_sync_codex_artifacts — Ingest Codex session logs into the artifact store
29. dhee_why              — Explain memory/artifact provenance and lineage
30. dhee_context_bootstrap — One read-only startup packet for Codex/agents
31. dhee_handoff          — Emit a structured resume snapshot for a new harness
32. dhee_inbox            — Fetch unread live shared-context broadcasts
33. dhee_broadcast        — Publish live shared context to the workspace line
34. dhee_submit_learning  — Submit an auditable learning candidate
35. dhee_search_learnings — Search promoted learnings or candidates on request
36. dhee_promote_learning — Promote a learning after gate/approval
37. dhee_context_*       — Compiled state, debt, checkpoint, rollover, provision
38. dhee_tools_list      — Discover compact vs full MCP surfaces
"""

import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ToolAnnotations

from dhee.mcp_registry import CONTEXT_COMPILER_TOOL_NAMES, make_tools
from dhee.memory.main import FullMemory
from dhee.configs.base import (
    MemoryConfig,
    VectorStoreConfig,
    LLMConfig,
    EmbedderConfig,
    FadeMemConfig,
)
from dhee.provider_defaults import (
    DEFAULT_COLLECTION,
    DEFAULT_NVIDIA_EMBEDDER_MODEL,
    DEFAULT_NVIDIA_LLM_MODEL,
    EMBEDDING_DIMS_BY_MODEL,
    embedding_dims_for,
)

logger = logging.getLogger(__name__)

_MCP_CONTEXT_FIRST_INSTRUCTIONS = (
    "Dhee is the native memory, context-router, and shared continuity layer. "
    "For substantive repo/workspace tasks, consult Dhee before reconstructing "
    "context from files or shell output: call dhee_context_bootstrap once with "
    "the absolute repo path. It bundles handoff, active shared task, shared "
    "results, and inbox. Fall back to dhee_handoff, dhee_shared_task, "
    "dhee_shared_task_results, and dhee_inbox only when exact legacy calls are "
    "needed. "
    "When the user says continue, resume, previous, shared "
    "context, or UI context, treat Dhee handoff/shared-task results as the "
    "source of continuity. Use dhee_broadcast to send live context another "
    "agent or project must see immediately. Search promoted playbooks with "
    "dhee_search_learnings when prior Dhee/Hermes evolution may apply. Prefer "
    "dhee_read, dhee_grep, and dhee_bash for large reusable reads/searches/"
    "commands so raw output stays behind pointers. When DHEE_HARNESS=codex, "
    "Dhee also syncs Codex session logs before context/collaboration reads so "
    "Codex native tool progress becomes shared Dhee context without a separate "
    "middleman agent."
)


def _default_user_id(args: Dict[str, Any]) -> str:
    return str(args.get("user_id") or os.environ.get("DHEE_USER_ID") or "default")


def _default_agent_id(args: Dict[str, Any]) -> str:
    return str(args.get("agent_id") or os.environ.get("DHEE_AGENT_ID") or "mcp-server")


def _default_requester_agent_id(args: Dict[str, Any]) -> str:
    return str(
        args.get("requester_agent_id")
        or os.environ.get("DHEE_REQUESTER_AGENT_ID")
        or _default_agent_id(args)
    )


def _default_source_app(args: Dict[str, Any]) -> str:
    return str(
        args.get("source_app")
        or os.environ.get("DHEE_SOURCE_APP")
        or _default_agent_id(args)
    )


def _maybe_sync_codex_runtime(arguments: Dict[str, Any]) -> Dict[str, Any] | None:
    """Best-effort incremental Codex sync before collaboration reads.

    Codex lacks Claude-style live hooks. When the active harness is Codex,
    Dhee opportunistically tails the persisted event stream before serving
    collaboration / handoff / artifact queries so the next MCP round sees
    post-tool results without a manual sync step.
    """
    harness_arg = arguments.get("harness")
    harness = str(
        harness_arg
        or os.environ.get("DHEE_HARNESS")
        or os.environ.get("DHEE_AGENT_ID")
        or ""
    ).strip().lower()
    if harness != "codex":
        return None
    auto_sync = arguments.get("codex_auto_sync")
    if auto_sync is None:
        auto_sync = os.environ.get("DHEE_CODEX_AUTO_SYNC")
    if harness_arg is None and str(auto_sync or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        from dhee.core.artifacts import ArtifactManager
        from dhee.core.codex_stream import sync_latest_codex_stream

        return sync_latest_codex_stream(
            ArtifactManager(get_db()),
            get_db(),
            user_id=_default_user_id(arguments),
            sessions_root=os.environ.get("DHEE_CODEX_SESSIONS_ROOT"),
            log_path=str(arguments.get("log_path") or "").strip() or None,
        )
    except Exception:
        return None


def _get_embedding_dims_for_model(model: str, provider: str) -> int:
    env_dims = os.environ.get("DHEE_EMBEDDING_DIMS") or os.environ.get("FADEM_EMBEDDING_DIMS")
    if env_dims:
        return int(env_dims)
    if model in EMBEDDING_DIMS_BY_MODEL:
        return EMBEDDING_DIMS_BY_MODEL[model]
    return embedding_dims_for(provider, model)


def _history_embedding_dims(history_db_path: str) -> Optional[int]:
    """Return dominant stored embedding dimension for an existing memory DB."""
    if not os.path.exists(history_db_path):
        return None
    try:
        conn = sqlite3.connect(history_db_path)
        try:
            rows = conn.execute(
                """
                SELECT embedding
                FROM memories
                WHERE tombstone = 0
                  AND embedding IS NOT NULL
                  AND embedding != ''
                LIMIT 200
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    counts: Dict[int, int] = {}
    for (raw_embedding,) in rows:
        try:
            parsed = json.loads(raw_embedding) if isinstance(raw_embedding, str) else raw_embedding
        except Exception:
            continue
        if isinstance(parsed, list) and parsed:
            counts[len(parsed)] = counts.get(len(parsed), 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def get_memory_instance() -> FullMemory:
    """Create and return a configured FullMemory instance for the MCP server."""
    from dhee.cli_config import get_api_key, load_config

    config_json = load_config()
    nvidia_key = get_api_key("nvidia")
    openai_key = get_api_key("openai")
    gemini_key = get_api_key("gemini")

    def _env(key: str, default: str = "") -> str:
        """Read DHEE_ env var with FADEM_ fallback for backward compat."""
        return os.environ.get(f"DHEE_{key}") or os.environ.get(f"FADEM_{key}") or default

    from dhee.configs.base import _dhee_data_dir
    configured_vector = config_json.get("vector_store") if isinstance(config_json, dict) else {}
    configured_vector_config = (
        dict(configured_vector.get("config") or {})
        if isinstance(configured_vector, dict)
        else {}
    )
    vec_db_path = _env("VEC_DB_PATH") or os.path.join(_dhee_data_dir(), "zvec")
    collection = _env(
        "COLLECTION",
        str(configured_vector_config.get("collection_name") or DEFAULT_COLLECTION),
    )
    history_db_path = _env("HISTORY_DB") or os.path.join(_dhee_data_dir(), "history.db")
    existing_embedding_dims = _history_embedding_dims(history_db_path)
    preserve_existing_dims = bool(config_json.get("preserve_existing_embedding_dims", False))

    provider_embedding_dims = embedding_dims_for("nvidia")
    if nvidia_key:
        embedder_model = _env(
            "EMBEDDER_MODEL",
            str(config_json.get("embedder_model") or DEFAULT_NVIDIA_EMBEDDER_MODEL),
        )
        provider_embedding_dims = _get_embedding_dims_for_model(embedder_model, "nvidia")
        embedding_dims = int(
            existing_embedding_dims
            if preserve_existing_dims and existing_embedding_dims
            else provider_embedding_dims
        )
        llm_config = LLMConfig(
            provider="nvidia",
            config={
                "model": _env("LLM_MODEL", str(config_json.get("llm_model") or DEFAULT_NVIDIA_LLM_MODEL)),
                "temperature": 0.2, "max_tokens": 4096, "api_key": nvidia_key,
            }
        )
        use_simple_embedder = bool(
            preserve_existing_dims
            and existing_embedding_dims
            and existing_embedding_dims != provider_embedding_dims
        )
        embedder_config = EmbedderConfig(
            provider="simple" if use_simple_embedder else "nvidia",
            config=(
                {"embedding_dims": embedding_dims}
                if use_simple_embedder
                else {
                    "model": embedder_model,
                    "api_key": nvidia_key,
                    "embedding_dims": embedding_dims,
                }
            ),
        )
    elif openai_key:
        embedder_model = _env("EMBEDDER_MODEL", "text-embedding-3-small")
        provider_embedding_dims = _get_embedding_dims_for_model(embedder_model, "openai")
        embedding_dims = int(existing_embedding_dims or provider_embedding_dims)
        llm_config = LLMConfig(
            provider="openai",
            config={
                "model": _env("LLM_MODEL", "gpt-4o-mini"),
                "temperature": 0.1, "max_tokens": 1024, "api_key": openai_key,
            }
        )
        embedder_config = EmbedderConfig(
            provider="simple" if existing_embedding_dims and existing_embedding_dims != provider_embedding_dims else "openai",
            config=(
                {"embedding_dims": embedding_dims}
                if existing_embedding_dims and existing_embedding_dims != provider_embedding_dims
                else {"model": embedder_model, "api_key": openai_key}
            ),
        )
    elif gemini_key:
        embedder_model = _env("EMBEDDER_MODEL", "gemini-embedding-001")
        provider_embedding_dims = _get_embedding_dims_for_model(embedder_model, "gemini")
        embedding_dims = int(existing_embedding_dims or provider_embedding_dims)
        llm_config = LLMConfig(
            provider="gemini",
            config={
                "model": _env("LLM_MODEL", "gemini-2.0-flash"),
                "temperature": 0.1, "max_tokens": 1024, "api_key": gemini_key,
            }
        )
        embedder_config = EmbedderConfig(
            provider="simple" if existing_embedding_dims and existing_embedding_dims != provider_embedding_dims else "gemini",
            config=(
                {"embedding_dims": embedding_dims}
                if existing_embedding_dims and existing_embedding_dims != provider_embedding_dims
                else {"model": embedder_model, "api_key": gemini_key}
            ),
        )
    else:
        embedding_dims = int(
            existing_embedding_dims
            if preserve_existing_dims and existing_embedding_dims
            else provider_embedding_dims
        )
        llm_config = LLMConfig(provider="mock", config={})
        embedder_config = EmbedderConfig(
            provider="simple", config={"embedding_dims": embedding_dims},
        )

    # Use in-memory vector store for simple embedder (no persistent storage needed)
    if embedder_config.provider == "simple" and not existing_embedding_dims:
        vector_store_config = VectorStoreConfig(
            provider="memory",
            config={
                "collection_name": collection,
                "embedding_model_dims": embedding_dims,
            },
        )
    else:
        vector_store_config = VectorStoreConfig(
            provider="zvec",
            config={
                "path": vec_db_path,
                "collection_name": collection,
                "embedding_model_dims": embedding_dims,
            },
        )

    fade_config = FadeMemConfig(
        enable_forgetting=_env("ENABLE_FORGETTING", "true").lower() == "true",
        sml_decay_rate=float(_env("SML_DECAY_RATE", "0.15")),
        lml_decay_rate=float(_env("LML_DECAY_RATE", "0.02")),
    )

    config = MemoryConfig(
        vector_store=vector_store_config,
        llm=llm_config,
        embedder=embedder_config,
        history_db_path=history_db_path,
        embedding_model_dims=embedding_dims,
        fade=fade_config,
    )
    if hasattr(config, "enrichment"):
        config.enrichment.defer_enrichment = True
        config.enrichment.enable_unified = True

    return FullMemory(config)


# Global instances (lazy)
_memory: Optional[FullMemory] = None
_db = None  # type: ignore
_buddhi = None  # type: ignore


def get_memory() -> FullMemory:
    global _memory
    if _memory is None:
        _memory = get_memory_instance()
    return _memory


def get_db():
    """Lazy singleton for direct SQLite access without model setup."""
    global _db
    if _db is None:
        from dhee.configs.base import _dhee_data_dir
        from dhee.db.sqlite import SQLiteManager

        _db = SQLiteManager(os.path.join(_dhee_data_dir(), "history.db"))
    return _db


def get_buddhi():
    """Lazy singleton for the Buddhi cognition layer."""
    global _buddhi
    if _buddhi is None:
        from dhee.configs.base import _dhee_data_dir
        from dhee.core.buddhi import Buddhi
        _buddhi = Buddhi(data_dir=os.path.join(_dhee_data_dir(), "buddhi"))
    return _buddhi


# ── MCP Server ──

server = Server("dhee", instructions=_MCP_CONTEXT_FIRST_INSTRUCTIONS)

# Tool definitions — growing contract, keep tests in sync
TOOLS = [
    Tool(
        name="remember",
        description="Quick-save a fact or preference to memory. Stores immediately with infer=False by default and uses the configured MCP agent/source identity when not provided.",
        inputSchema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact or preference to remember"},
                "user_id": {"type": "string", "description": "User identifier (defaults to DHEE_USER_ID or 'default')."},
                "agent_id": {"type": "string", "description": "Agent identifier (defaults to DHEE_AGENT_ID or 'mcp-server')."},
                "source_app": {"type": "string", "description": "Source application label (defaults to DHEE_SOURCE_APP or agent_id)."},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Optional categories to tag this memory with (e.g., ['preferences', 'coding'])"},
                "context": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                    "description": "Recent conversation turns (sliding window) for richer memory context",
                },
            },
            "required": ["content"],
        },
    ),
    Tool(
        name="search_memory",
        description=(
            "Search memory for relevant memories by semantic query. Use before "
            "local reconstruction when prior repo/user context may exist, and "
            "for explicit user recall requests such as 'what did we discuss "
            "about X?' or 'recall my preference for Y'. The Claude Code "
            "UserPromptSubmit hook also handles background search automatically."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query - what you're trying to remember"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "agent_id": {"type": "string", "description": "Agent identifier to scope search to (optional)"},
                "repo": {"type": "string", "description": "Optional repository/workspace path for repo handoff continuity."},
                "limit": {"type": "integer", "description": "Maximum number of results to return (default: 10)"},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Filter results by categories"},
                "orchestration_mode": {
                    "type": "string",
                    "enum": ["off", "hybrid", "strict"],
                    "description": "Optional orchestrated retrieval mode (default: off for backward compatibility).",
                },
                "question_type": {"type": "string", "description": "Optional question type for intent routing."},
                "question_date": {"type": "string", "description": "Optional question date for temporal reasoning."},
                "answer_context_top_k": {"type": "integer", "description": "Context result cap in orchestrated mode (default: 10)."},
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
        name="dhee_context",
        description=(
            "HyperAgent session bootstrap. Call at conversation start, before "
            "local reconstruction, to get performance trends, synthesized "
            "insights from prior runs, relevant skills, pending intentions, "
            "proactive warnings, and top memories. This single call turns any "
            "agent into a HyperAgent with persistent memory and self-improvement awareness."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier to load context for (default: 'default')"},
                "task_description": {"type": "string", "description": "What the agent is about to work on — used to filter relevant insights, skills, and performance history"},
                "repo": {"type": "string", "description": "Optional repo/workspace root to scope promoted learnings"},
                "limit": {"type": "integer", "description": "Maximum number of memories to return (default: 10)"},
            },
        },
    ),
    Tool(
        name="dhee_scene_world_route",
        description=(
            "Predict likely outcomes for candidate next actions using the optional "
            "SceneWorld world-model sidecar. Use before choosing a high-stakes "
            "agent action when DHEE_SCENE_WORLD_ENABLED=1."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Current task or scene"},
                "query": {"type": "string", "description": "Alias for task"},
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "user_id": {"type": "string", "description": "User identifier"},
                "harness": {"type": "string", "description": "Harness/runtime id"},
                "top_k": {"type": "integer", "description": "Number of ranked actions to return"},
                "record": {"type": "boolean", "description": "Record the route trace when route logging is configured"},
            },
        },
    ),
    Tool(
        name="dhee_scene_compile",
        description="Compile a private TemporalScene card from evidence pointers, memory rows, agent outputs, browser captures, or admitted derivatives.",
        inputSchema={
            "type": "object",
            "properties": {
                "evidence": {"type": "array", "items": {"type": "object"}, "description": "Evidence rows or pointers to compile."},
                "query": {"type": "string", "description": "Optional query/task used when include_recent_memories is true."},
                "task": {"type": "string", "description": "Current user task or scene goal."},
                "title": {"type": "string", "description": "Optional scene title."},
                "repo": {"type": "string", "description": "Repo/workspace path to attach as a scene ref."},
                "user_id": {"type": "string", "description": "User id (default: default)."},
                "privacy_scope": {"type": "string", "description": "Scene privacy scope (default: personal)."},
                "store_dir": {"type": "string", "description": "Optional scene store override."},
                "save": {"type": "boolean", "description": "Persist scene to the private scene store (default: true)."},
                "include_recent_memories": {"type": "boolean", "description": "If evidence is empty, search recent/relevant memory and compile from those results."},
                "include_repo_context": {"type": "boolean", "description": "Collect repo-shared context entries as evidence."},
                "include_session": {"type": "boolean", "description": "Fetch latest compact session digest as evidence when available."},
                "include_shared_task_results": {"type": "boolean", "description": "Fetch active shared-task result packets as evidence when available."},
                "include_artifacts": {"type": "boolean", "description": "Fetch recent artifact summaries as evidence when available."},
                "include_live_sources": {"type": "boolean", "description": "Fetch session, shared-task results, and artifacts for requested sources."},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Evidence sources: evidence, memory, repo_context, session, shared_task_results, artifacts."},
                "session": {"type": "object", "description": "Optional session digest to compile as evidence."},
                "shared_task_results": {"description": "Optional shared-task result rows or response object."},
                "artifacts": {"description": "Optional artifact summaries or response object."},
                "limit": {"type": "integer", "description": "Memory/evidence limit when include_recent_memories is true."},
            },
        },
    ),
    Tool(
        name="dhee_scene_search",
        description="Search private TemporalScene cards and return prompt-safe summaries with evidence refs only.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "user_id": {"type": "string"},
                "repo": {"type": "string"},
                "limit": {"type": "integer"},
                "store_dir": {"type": "string"},
                "include_personal": {"type": "boolean"},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="dhee_context_pack",
        description="Build a hard-budget context pack from ranked scene cards. Raw evidence expands only by pointer.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "user_id": {"type": "string"},
                "repo": {"type": "string"},
                "token_budget": {"type": "integer"},
                "limit": {"type": "integer"},
                "store_dir": {"type": "string"},
                "include_personal": {"type": "boolean"},
            },
            "required": ["query"],
        },
    ),
    *make_tools(Tool, CONTEXT_COMPILER_TOOL_NAMES),
    Tool(
        name="get_last_session",
        description=(
            "Get the most recent session digest to continue where the last "
            "agent left off. Search this before local reconstruction when a "
            "repo task may have prior context. Returns full handoff context "
            "including linked memories."
        ),
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
        description="Get statistics about the memory store including counts and layer distribution. Call when the user asks about memory health, wants an overview of what's stored, or runs /dhee:status.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier to get stats for (default: all users)"},
                "agent_id": {"type": "string", "description": "Agent identifier to scope stats to (optional)"},
            },
        },
    ),
    Tool(
        name="audit_memory_quality",
        description="Read-only readiness audit for Dhee's personal-memory quality: canonical model coverage, noise isolation, queued/degraded writes, evidence distillation, and repair recommendations.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier to audit (default: DHEE_USER_ID/all)"},
                "agent_id": {"type": "string", "description": "Optional agent scope"},
                "limit": {"type": "integer", "description": "Maximum memories to scan (default 10000)"},
                "profile_keyword": {"type": "string", "description": "Optional keyword, e.g. 'chotu', that must appear in canonical personal memories"},
                "require_personal_model": {"type": "boolean", "description": "Require canonical/LML personal-model checks (default true)"},
            },
        },
    ),
    Tool(
        name="repair_memory_quality",
        description="Dry-run or apply Dhee's memory-quality repair: promote canonical personal memories, isolate passive/test noise, and reopen degraded queued rows.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier to repair (default: DHEE_USER_ID/all)"},
                "agent_id": {"type": "string", "description": "Optional agent scope"},
                "limit": {"type": "integer", "description": "Maximum memories to scan (default 10000)"},
                "dry_run": {"type": "boolean", "description": "Preview repairs without writing (default true)"},
                "reindex_vectors": {"type": "boolean", "description": "Also reconcile vector entries from authoritative DB rows"},
            },
        },
    ),
    Tool(
        name="search_skills",
        description="Search for reusable skills by semantic query. Returns matching skills with confidence scores and metadata.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What kind of skill are you looking for"},
                "limit": {"type": "integer", "description": "Maximum number of results (default: 5)"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by tags"},
                "min_confidence": {"type": "number", "description": "Minimum confidence threshold (default: 0.0)"},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="apply_skill",
        description="Apply a skill by ID. Returns the skill recipe as injectable markdown for agent context.",
        inputSchema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "The ID of the skill to apply"},
            },
            "required": ["skill_id"],
        },
    ),
    Tool(
        name="log_skill_outcome",
        description="Report success or failure for a skill. Updates the skill's confidence score based on outcome. Optionally accepts per-step outcomes for granular feedback.",
        inputSchema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "The ID of the skill to log outcome for"},
                "success": {"type": "boolean", "description": "Whether the skill application was successful"},
                "notes": {"type": "string", "description": "Optional notes about the outcome"},
                "step_outcomes": {
                    "type": "array",
                    "description": "Optional per-step outcomes for granular feedback",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_index": {"type": "integer", "description": "Index of the step (0-based)"},
                            "success": {"type": "boolean", "description": "Whether this step succeeded"},
                            "failure_type": {"type": "string", "enum": ["structural", "slot"], "description": "Type of failure"},
                            "failed_slot": {"type": "string", "description": "Which slot caused the failure"},
                            "notes": {"type": "string", "description": "Notes about this step's outcome"},
                        },
                        "required": ["step_index", "success"],
                    },
                },
            },
            "required": ["skill_id", "success"],
        },
    ),
    Tool(
        name="record_trajectory_step",
        description="Record an action step in the active trajectory. Use start_trajectory first (via mine_skills with task_description) to begin recording.",
        inputSchema={
            "type": "object",
            "properties": {
                "recorder_id": {"type": "string", "description": "The recorder ID returned by start_trajectory"},
                "action": {"type": "string", "description": "The action performed (e.g., 'search', 'edit', 'test')"},
                "tool": {"type": "string", "description": "The tool used (e.g., 'grep', 'write', 'pytest')"},
                "args": {"type": "object", "description": "Arguments passed to the tool"},
                "result_summary": {"type": "string", "description": "Brief summary of the result"},
                "error": {"type": "string", "description": "Error message if the step failed"},
            },
            "required": ["recorder_id", "action"],
        },
    ),
    Tool(
        name="mine_skills",
        description="Run a skill mining cycle. Analyzes successful trajectories and extracts reusable skills. Can also start/complete trajectory recording.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["mine", "start_trajectory", "complete_trajectory"],
                    "description": "Action to perform: 'mine' runs mining, 'start_trajectory' begins recording, 'complete_trajectory' finalizes recording",
                },
                "task_query": {"type": "string", "description": "Filter trajectories by task description (for mining)"},
                "task_description": {"type": "string", "description": "Task description (for start_trajectory)"},
                "recorder_id": {"type": "string", "description": "Recorder ID (for complete_trajectory)"},
                "success": {"type": "boolean", "description": "Whether the task succeeded (for complete_trajectory)"},
                "outcome_summary": {"type": "string", "description": "Brief outcome description (for complete_trajectory)"},
            },
        },
    ),
    Tool(
        name="get_skill_stats",
        description="Get statistics about skills and trajectories including counts, confidence averages, and active recordings.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="search_skills_structural",
        description="Find skills by structural similarity to given steps. Decomposes the query steps into a recipe template and matches against skills with structural decomposition.",
        inputSchema={
            "type": "object",
            "properties": {
                "query_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Steps to find structurally similar skills for (e.g., ['Build Go app', 'Run go test', 'Deploy to GCP'])",
                },
                "limit": {"type": "integer", "description": "Maximum number of results (default: 5)"},
                "min_similarity": {"type": "number", "description": "Minimum structural similarity threshold 0.0-1.0 (default: 0.3)"},
            },
            "required": ["query_steps"],
        },
    ),
    Tool(
        name="analyze_skill_gaps",
        description="Analyze what transfers from a skill to a new target context. Shows proven bindings, untested bindings, and missing slots with recommendations.",
        inputSchema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "The ID of the skill to analyze"},
                "target_context": {
                    "type": "object",
                    "description": "Target context with slot values (e.g., {'language': 'go', 'deploy_target': 'gcp'})",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["skill_id", "target_context"],
        },
    ),
    Tool(
        name="decompose_skill",
        description="Trigger structural decomposition of a flat skill into recipe + ingredients. Extracts slots and creates structured step templates.",
        inputSchema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "The ID of the skill to decompose"},
            },
            "required": ["skill_id"],
        },
    ),
    Tool(
        name="apply_skill_with_bindings",
        description="Apply a skill with specific slot values. Renders steps with bindings and includes gap analysis showing proven vs untested components.",
        inputSchema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "The ID of the skill to apply"},
                "bindings": {
                    "type": "object",
                    "description": "Slot bindings (e.g., {'language': 'go', 'test_framework': 'go test', 'deploy_target': 'gcp'})",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["skill_id", "bindings"],
        },
    ),
    Tool(
        name="enrich_pending",
        description="Batch-enrich memories stored with deferred enrichment. Runs echo, category, entity, and profile extraction in batched LLM calls. Use after bulk ingestion to retroactively enrich memories.",
        inputSchema={
            "type": "object",
            "properties": {
                "batch_size": {"type": "integer", "description": "Memories per LLM call (default: 10)"},
                "max_batches": {"type": "integer", "description": "Max batches to process (default: 5)"},
            },
        },
    ),
    Tool(
        name="think",
        description="Cognitive decomposition — memory-grounded reasoning. Decomposes a complex question into sub-questions, grounds each in memory, and synthesizes an answer from verified facts. Use for questions that require reasoning across multiple memories.",
        inputSchema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to reason about"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "repo": {"type": "string", "description": "Optional repository/workspace path for repo handoff continuity."},
                "max_depth": {"type": "integer", "description": "Maximum decomposition depth (default: 3)"},
            },
            "required": ["question"],
        },
    ),
    Tool(
        name="anticipate",
        description="Proactive intelligence — surfaces triggered intentions, upcoming scenes, and relevant insights. Buddhi checks what you need before you ask. Use at session start or periodically.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "context": {"type": "string", "description": "Current task or query context for intention matching"},
            },
        },
    ),
    Tool(
        name="record_outcome",
        description="Report a task outcome for performance tracking. Buddhi records the score, detects trends (regressions, breakthroughs), and auto-generates insights. Call after completing any measurable task.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_type": {"type": "string", "description": "Category of the task (e.g., 'code_review', 'bug_fix', 'refactor')"},
                "score": {"type": "number", "description": "Outcome score 0.0-1.0 (1.0 = perfect)"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "metadata": {"type": "object", "description": "Optional metadata about the outcome"},
            },
            "required": ["task_type", "score"],
        },
    ),
    Tool(
        name="reflect",
        description="Agent-triggered reflection — synthesize insights from experience. Call when a task completes to record what worked, what failed, and key decisions. These become transferable insights that improve future runs across domains.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_type": {"type": "string", "description": "Category of the task"},
                "what_worked": {"type": "string", "description": "What approach or strategy worked well"},
                "what_failed": {"type": "string", "description": "What approach or strategy failed"},
                "key_decision": {"type": "string", "description": "A key decision made and its rationale"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
            },
            "required": ["task_type"],
        },
    ),
    Tool(
        name="store_intention",
        description="Store a future trigger — prospective memory. When the agent or user says 'remember to X when Y', store it as an intention. Buddhi will surface it when the trigger condition matches.",
        inputSchema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What to remember to do"},
                "trigger_keywords": {"type": "array", "items": {"type": "string"}, "description": "Keywords that trigger this intention"},
                "trigger_after": {"type": "string", "description": "ISO timestamp deadline for time-based triggers"},
                "action_type": {"type": "string", "enum": ["remind", "suggest", "warn"], "description": "How to surface: remind (neutral), suggest (positive), warn (caution)"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
            },
            "required": ["description"],
        },
    ),
    Tool(
        name="dhee_submit_learning",
        description="Submit an auditable Dhee learning candidate. Candidates are never injected into context until promoted.",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short learning title"},
                "body": {"type": "string", "description": "Reusable tactic, skill, outcome, or playbook"},
                "kind": {"type": "string", "enum": ["skill", "heuristic", "policy", "contrast", "memory", "workflow", "playbook"], "description": "Learning kind"},
                "source_agent_id": {"type": "string", "description": "Agent that discovered the learning"},
                "source_harness": {"type": "string", "description": "Harness that produced the learning"},
                "task_type": {"type": "string", "description": "Task category"},
                "repo": {"type": "string", "description": "Optional repo/workspace root"},
                "scope": {"type": "string", "enum": ["personal", "repo", "workspace"], "description": "Desired scope after promotion"},
                "confidence": {"type": "number", "description": "Initial confidence 0.0-1.0"},
                "utility": {"type": "number", "description": "Initial utility 0.0-1.0"},
                "evidence": {"type": "array", "items": {"type": "object"}, "description": "Supporting evidence records"},
            },
            "required": ["title", "body"],
        },
    ),
    Tool(
        name="dhee_search_learnings",
        description="Search promoted Dhee learnings. Set include_candidates=true only for explicit review or approval workflows.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Learning search query"},
                "task_type": {"type": "string", "description": "Optional task type filter"},
                "repo": {"type": "string", "description": "Optional repo/workspace root"},
                "status": {"type": "string", "enum": ["candidate", "promoted", "rejected", "archived"], "description": "Status filter when candidates are included"},
                "include_candidates": {"type": "boolean", "description": "Include candidate learnings in search"},
                "limit": {"type": "integer", "description": "Maximum results (default 10)"},
            },
        },
    ),
    Tool(
        name="dhee_promote_learning",
        description="Promote a learning after gate/approval. Repo and workspace promotions require explicit approval.",
        inputSchema={
            "type": "object",
            "properties": {
                "learning_id": {"type": "string", "description": "Learning candidate id"},
                "scope": {"type": "string", "enum": ["personal", "repo", "workspace"], "description": "Promotion scope"},
                "repo": {"type": "string", "description": "Repo root when scope=repo"},
                "approved_by": {"type": "string", "description": "Approval identity for repo/workspace or manual promotion"},
            },
            "required": ["learning_id"],
        },
    ),
    Tool(
        name="dhee_context_status",
        description="Show compiled-state health, projected context debt, and rollover status for a repo.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id"},
                "user_id": {"type": "string", "description": "User identifier"},
                "agent_id": {"type": "string", "description": "Agent identity"},
            },
        },
    ),
    Tool(
        name="dhee_context_state",
        description="Return the living Dhee state card or canonical compiled state for the current repo.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id"},
                "user_id": {"type": "string", "description": "User identifier"},
                "agent_id": {"type": "string", "description": "Agent identity"},
                "format": {"type": "string", "enum": ["card", "markdown", "json"], "description": "Return format (default card)"},
            },
        },
    ),
    Tool(
        name="dhee_context_checkpoint",
        description="Write a compact compiled-state checkpoint for continuation or compaction.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id"},
                "user_id": {"type": "string", "description": "User identifier"},
                "agent_id": {"type": "string", "description": "Agent identity"},
                "reason": {"type": "string", "description": "Checkpoint reason"},
            },
        },
    ),
    Tool(
        name="dhee_context_rollover",
        description="Create a checkpoint and return instructions for continuing from compiled state.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id"},
                "user_id": {"type": "string", "description": "User identifier"},
                "agent_id": {"type": "string", "description": "Agent identity"},
                "reason": {"type": "string", "description": "Rollover reason"},
            },
        },
    ),
    Tool(
        name="dhee_context_provision",
        description="Estimate raw vs compiled context cost before starting a task. Does not change state.",
        inputSchema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task or prompt to estimate"},
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id"},
                "user_id": {"type": "string", "description": "User identifier"},
                "agent_id": {"type": "string", "description": "Agent identity"},
            },
        },
    ),
    Tool(
        name="dhee_tools_list",
        description="List compact default MCP tools and advanced tools available in dhee-mcp-full.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="dhee_shell",
        description=(
            "Run one approved DheeFS virtual shell command over Dhee's learning/context space. "
            "Supports ls, cat, grep, why, promote, reject, broadcast, provision, and snapshot. "
            "No bash pipes or native filesystem access."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "DheeFS command, e.g. `cat /handoff/latest.md`"},
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id override"},
                "user_id": {"type": "string", "description": "User identifier (default: default)"},
                "agent_id": {"type": "string", "description": "Agent identity for mutating commands"},
            },
            "required": ["command"],
        },
    ),
    Tool(
        name="dhee_list_assets",
        description=(
            "List host-parsed artifacts stored by Dhee. Returns compact "
            "summaries: filename, lifecycle state, bindings, extraction count, "
            "and latest extraction time. Use this to discover what uploaded "
            "files are already reusable before re-uploading or re-reading."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "workspace_id": {"type": "string", "description": "Optional absolute workspace root filter"},
                "folder_path": {"type": "string", "description": "Optional folder-local filter within a workspace"},
                "limit": {"type": "integer", "description": "Maximum results to return (default 20, max 200)"},
            },
        },
    ),
    Tool(
        name="dhee_get_asset",
        description=(
            "Inspect a stored artifact by `artifact_id` or `source_path`. "
            "Returns summary metadata, bindings, and extraction/chunk summaries. "
            "Chunk and extraction bodies are omitted by default to keep context "
            "small; opt in explicitly when you genuinely need the raw extracted content."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "Artifact identifier returned by dhee_list_assets"},
                "source_path": {"type": "string", "description": "Original absolute source path to resolve an artifact when id is unknown"},
                "workspace_id": {"type": "string", "description": "Optional workspace scope when resolving by source_path"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "include_extraction_text": {"type": "boolean", "description": "Include extracted text in the response (default false)"},
                "include_chunks": {"type": "boolean", "description": "Include chunk records in the response (default false)"},
                "chunk_limit": {"type": "integer", "description": "Maximum number of chunks to include when include_chunks=true (default 5, max 50)"},
                "max_text_chars": {"type": "integer", "description": "Per extraction/chunk text cap when bodies are included (default 1200, max 12000)"},
            },
        },
    ),
    Tool(
        name="dhee_sync_codex_artifacts",
        description=(
            "Ingest Codex session logs into Dhee's artifact store using the "
            "first successful host parse contract. Bare file references become "
            "`attached`; successful read/parse tool outputs become durable "
            "artifact extractions and chunks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "log_path": {"type": "string", "description": "Optional absolute path to a Codex session .jsonl log (defaults to latest)"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
            },
        },
    ),
    Tool(
        name="dhee_why",
        description=(
            "Explain why a memory or artifact exists using stored history, "
            "artifact provenance, and distillation lineage. Read-only and "
            "no-LLM: this is for inspectability, debugging, and portability audits."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "Memory ID or artifact ID to explain"},
                "history_limit": {"type": "integer", "description": "Maximum memory_history rows to include (default 10, max 50)"},
                "include_extraction_text": {"type": "boolean", "description": "For artifact IDs, include extracted text bodies (default false)"},
                "include_chunks": {"type": "boolean", "description": "For artifact IDs, include chunk bodies (default false)"},
                "chunk_limit": {"type": "integer", "description": "Maximum artifact chunks to include when include_chunks=true (default 5, max 50)"},
                "max_text_chars": {"type": "integer", "description": "Per extraction/chunk text cap (default 1200, max 12000)"},
            },
            "required": ["identifier"],
        },
    ),
    Tool(
        name="dhee_thread_state",
        description=(
            "Read, update, or clear the lightweight live continuity state for a "
            "single harness/app thread. This is the cheap per-thread bootstrap "
            "layer Dhee should prefer before falling back to `get_last_session`."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Harness or app thread identifier"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "repo": {"type": "string", "description": "Optional repo/workspace root"},
                "workspace_id": {"type": "string", "description": "Optional workspace scope override"},
                "folder_path": {"type": "string", "description": "Optional folder-local scope"},
                "status": {"type": "string", "description": "Thread status such as active or paused"},
                "summary": {"type": "string", "description": "Compact thread summary"},
                "current_goal": {"type": "string", "description": "Current thread goal"},
                "current_step": {"type": "string", "description": "Current next step"},
                "session_id": {"type": "string", "description": "Optional harness session identifier"},
                "handoff_session_id": {"type": "string", "description": "Optional linked cross-agent handoff session id"},
                "metadata": {"type": "object", "description": "Optional arbitrary JSON metadata"},
                "clear": {"type": "boolean", "description": "Delete the thread state instead of reading/updating"},
            },
            "required": ["thread_id"],
        },
    ),
    Tool(
        name="dhee_shared_task",
        description=(
            "Create, inspect, list, or close the active shared collaboration task "
            "for a repo/workspace. Shared tasks scope the ephemeral cross-agent "
            "tool-result feed: one active shared task per repo/workspace, transient "
            "results during the task, durable memory/artifacts promoted separately."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "show", "list", "close"],
                    "description": "Operation to perform (default: show)",
                },
                "shared_task_id": {"type": "string", "description": "Explicit shared task identifier"},
                "title": {"type": "string", "description": "Task title for action=create"},
                "repo": {"type": "string", "description": "Optional repo/workspace root used to resolve the active task"},
                "workspace_id": {"type": "string", "description": "Optional workspace scope override"},
                "folder_path": {"type": "string", "description": "Optional folder-local scope"},
                "metadata": {"type": "object", "description": "Optional JSON metadata"},
                "keep_results": {"type": "boolean", "description": "For action=close, keep ephemeral results instead of pruning them"},
                "limit": {"type": "integer", "description": "For action=list, maximum tasks to return (default 20, max 100)"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
            },
        },
    ),
    Tool(
        name="dhee_shared_task_results",
        description=(
            "Inspect the ephemeral cross-agent tool-result feed for a shared repo "
            "task. This is the live collaboration window: in-flight claims plus "
            "completed digests/pointers, not durable memory."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "shared_task_id": {"type": "string", "description": "Explicit shared task identifier"},
                "repo": {"type": "string", "description": "Optional repo/workspace root used to resolve the active task"},
                "limit": {"type": "integer", "description": "Maximum results to return (default 10, max 100)"},
                "result_status": {
                    "type": "string",
                    "enum": ["in_flight", "completed", "abandoned"],
                    "description": "Optional status filter",
                },
                "packet_kind": {"type": "string", "description": "Optional packet-kind filter"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
            },
        },
    ),
    Tool(
        name="dhee_inbox",
        description=(
            "Fetch unread live shared-context broadcasts for this active agent. "
            "Call this after dhee_handoff/shared-task checks and after substantial "
            "tool work on shared tasks; the response includes a signal when another "
            "party has broadcast context that must be read before continuing."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo/workspace path used to resolve the live workspace"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id or path override"},
                "project_id": {"type": "string", "description": "Optional project/channel scope"},
                "channel": {"type": "string", "description": "Optional channel filter"},
                "consumer_id": {"type": "string", "description": "Stable consumer id; defaults to agent/session identity"},
                "agent_id": {"type": "string", "description": "Agent identity for own-message filtering"},
                "harness": {"type": "string", "description": "Harness/runtime id, e.g. codex or claude-code"},
                "session_id": {"type": "string", "description": "Native active session id"},
                "limit": {"type": "integer", "description": "Maximum unread messages to return (default 10, max 50)"},
                "mark_read": {"type": "boolean", "description": "Mark returned messages as read (default true)"},
                "include_own": {"type": "boolean", "description": "Include messages emitted by this same agent/session"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
            },
        },
    ),
    Tool(
        name="dhee_broadcast",
        description=(
            "Publish live shared context to the workspace line so other active "
            "agents and UI subscribers receive it immediately. Use for handoffs, "
            "discoveries, blocker notices, and cross-project messages that should "
            "not wait for session end."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "Broadcast body/message"},
                "title": {"type": "string", "description": "Short title"},
                "repo": {"type": "string", "description": "Repo/workspace path used to resolve the live workspace"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id or path override"},
                "project_id": {"type": "string", "description": "Source project id"},
                "target_project_id": {"type": "string", "description": "Optional target project id"},
                "channel": {"type": "string", "description": "Optional channel, defaults to project/workspace"},
                "message_kind": {"type": "string", "description": "Kind label, default broadcast"},
                "session_id": {"type": "string", "description": "Native active session id"},
                "task_id": {"type": "string", "description": "Related shared task id"},
                "metadata": {"type": "object", "description": "Optional JSON metadata"},
                "agent_id": {"type": "string", "description": "Agent identity publishing the broadcast"},
                "harness": {"type": "string", "description": "Harness/runtime id, e.g. codex or claude-code"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
            },
            "required": ["body"],
        },
    ),
    Tool(
        name="dhee_context_bootstrap",
        description=(
            "Read-only Codex startup packet. Use once at the start of repo work "
            "instead of separate dhee_handoff, dhee_shared_task, "
            "dhee_shared_task_results, and dhee_inbox calls."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id or path override"},
                "project_id": {"type": "string", "description": "Optional project/channel scope"},
                "thread_id": {"type": "string", "description": "Optional live thread id"},
                "shared_task_id": {"type": "string", "description": "Optional shared task id"},
                "agent_id": {"type": "string", "description": "Agent identity"},
                "harness": {"type": "string", "description": "Harness/runtime id, e.g. codex or claude-code"},
                "session_id": {"type": "string", "description": "Native active session id"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "memory_limit": {"type": "integer", "description": "Recent memories to include (default 5, max 20)"},
                "artifact_limit": {"type": "integer", "description": "Recent artifacts to include (default 5, max 20)"},
                "task_limit": {"type": "integer", "description": "Recent tasks to include (default 5, max 20)"},
                "intention_limit": {"type": "integer", "description": "Active intentions to include (default 5, max 20)"},
                "result_limit": {"type": "integer", "description": "Shared task results to include (default 10, max 100)"},
                "inbox_limit": {"type": "integer", "description": "Unread broadcasts to include (default 10, max 50)"},
                "include_own": {"type": "boolean", "description": "Include own live broadcasts"},
            },
        },
    ),
    Tool(
        name="dhee_handoff",
        description=(
            "Emit a structured handoff snapshot for cross-harness or cross-machine "
            "resume. Use this before shell/file exploration on substantive "
            "repo tasks. Prefers live thread state when `thread_id` is provided; "
            "otherwise falls back to the latest session digest plus active "
            "tasks/intentions, recent memories, and recent artifacts. Read-only and no-LLM."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "repo": {"type": "string", "description": "Optional repo/workspace root to scope session + artifact hints"},
                "thread_id": {"type": "string", "description": "Optional live thread identifier to prefer thread-native continuity"},
                "memory_limit": {"type": "integer", "description": "Recent memories to include (default 5, max 20)"},
                "artifact_limit": {"type": "integer", "description": "Recent artifacts to include (default 5, max 20)"},
                "task_limit": {"type": "integer", "description": "Recent tasks to include (default 5, max 20)"},
                "intention_limit": {"type": "integer", "description": "Active intentions to include (default 5, max 20)"},
            },
        },
    ),
    Tool(
        name="dhee_read",
        description=(
            "Router wrapper for Read. Opens a file, extracts a factual digest "
            "(path + line/char/token counts, symbols for Python/Markdown/JSON, "
            "head+tail excerpt), stores the full raw content under a pointer "
            "`ptr`, and returns a digest. Use this INSTEAD OF native `Read` "
            "to keep large file contents out of the conversation context. If "
            "`dhee_expand_result` is unavailable, rerun with `offset`+`limit`; "
            "explicit bounded ranges include a capped `source_window` inline."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file"},
                "offset": {"type": "integer", "description": "1-indexed start line (optional)"},
                "limit": {"type": "integer", "description": "Number of lines to read from offset (optional)"},
                "include_source": {"type": "boolean", "description": "Include a capped line-numbered source_window inline. Full raw still stays behind ptr."},
                "max_source_lines": {"type": "integer", "description": "Max source_window lines (cap 120)."},
                "max_source_chars": {"type": "integer", "description": "Max source_window chars (cap 12000)."},
                "digest_depth": {
                    "type": "string",
                    "enum": ["shallow", "normal", "deep"],
                    "description": "shallow=counts+symbols only; normal=+5-line head/tail; deep=+10-line head/tail. Default: normal",
                },
                "query": {"type": "string", "description": "Optional task/query for task-aware digest schema"},
                "task_intent": {"type": "string", "description": "Optional digest intent: find_definition, debug_failure, understand_module, inspect_config, general"},
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="dhee_bash",
        description=(
            "Router wrapper for Bash. Executes a shell command, captures "
            "stdout/stderr/exit, classifies the command (git_log, pytest, "
            "listing, grep, generic), and returns a class-aware digest. "
            "Full raw output is stored under `ptr` for later expansion. Use "
            "INSTEAD OF native `Bash` for any command that might produce "
            "large output (git log, pytest, find, grep)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "cwd": {"type": "string", "description": "Working directory (optional)"},
                "timeout": {"type": "number", "description": "Seconds before SIGKILL (default 120, max 600)"},
                "preview_only": {"type": "boolean", "description": "Return preflight risk without executing"},
            },
            "required": ["command"],
        },
    ),
    Tool(
        name="dhee_agent",
        description=(
            "Router wrapper for long-text tool returns (subagent results, "
            "pasted docs). Extracts file:line refs, headings, bullets, error "
            "signals, head+tail; stores the full raw under `ptr`."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Raw text to digest"},
                "kind": {"type": "string", "description": "Optional hint"},
                "source": {"type": "string", "description": "Optional label"},
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="dhee_grep",
        description=(
            "Router wrapper for pattern search. Runs ripgrep (or a Python "
            "fallback) over `path`, returns a digest: match count + top "
            "file:line hits + per-file density. Full hit list stays behind "
            "`ptr` for expansion. Use INSTEAD OF native `Grep` or "
            "`rg`/`grep -r` under `dhee_bash` for large codebase searches."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex (or literal if fixed_string=true) to search for"},
                "path": {"type": "string", "description": "File or directory root (default '.')"},
                "glob": {"type": "string", "description": "Optional glob filter, e.g. '*.py'"},
                "case_insensitive": {"type": "boolean", "description": "Case-insensitive search (rg -i)"},
                "fixed_string": {"type": "boolean", "description": "Treat pattern as a literal string, not regex"},
                "multiline": {"type": "boolean", "description": "Enable multiline matching (rg -U)"},
                "context": {"type": "integer", "description": "Lines of surrounding context (rg -C)"},
            },
            "required": ["pattern"],
        },
    ),
    Tool(
        name="dhee_expand_result",
        description=(
            "Retrieve the full raw content previously stored by a dhee_* "
            "router tool, identified by its `ptr` (e.g. 'R-1a2b3c4d'). Raw "
            "content will re-enter the context — only call when the digest "
            "was genuinely insufficient."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ptr": {"type": "string", "description": "Pointer returned by a dhee_* tool"},
                "range": {"description": "Optional 1-indexed line range, e.g. '40:80' or [40, 80]"},
                "symbol": {"type": "string", "description": "Optional function/class symbol to expand instead of full raw"},
                "reason": {"type": "string", "description": "Why the digest was insufficient; used to tune reducers"},
                "expected": {"type": "string", "description": "What signal you expected to find in the expansion"},
            },
            "required": ["ptr"],
        },
    ),
]


_READ_ONLY_TOOL_HINTS = {
    "search_memory",
    "get_memory",
    "get_all_memories",
    "dhee_context",
    "get_last_session",
    "get_memory_stats",
    "search_skills",
    "apply_skill",
    "get_skill_stats",
    "search_skills_structural",
    "analyze_skill_gaps",
    "decompose_skill",
    "apply_skill_with_bindings",
    "think",
    "anticipate",
    "dhee_search_learnings",
    "dhee_context_status",
    "dhee_context_state",
    "dhee_scene_search",
    "dhee_context_pack",
    "dhee_repo_brain_get",
    "dhee_repo_brain_localize",
    "dhee_task_contract_compile",
    "dhee_task_contract_list",
    "dhee_task_contract_get",
    "dhee_task_contract_interpret",
    "dhee_contract_runtime_status",
    "dhee_contract_enforcement_status",
    "dhee_contract_runtime_doctor",
    "dhee_update_capsule_list",
    "dhee_update_capsule_get",
    "dhee_update_capsule_interpret",
    "dhee_tools_list",
    "dhee_shell",
    "dhee_list_assets",
    "dhee_get_asset",
    "dhee_why",
    "dhee_shared_task_results",
    "dhee_inbox",
    "dhee_context_bootstrap",
    "dhee_handoff",
    "dhee_read",
    "dhee_grep",
    "dhee_agent",
    "dhee_expand_result",
}

for _tool in TOOLS:
    if _tool.name in _READ_ONLY_TOOL_HINTS:
        _tool.annotations = ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )


# ── Tool Handlers ──

def _handle_remember(memory, args):
    return memory.add(
        messages=args.get("content", ""),
        user_id=_default_user_id(args),
        agent_id=_default_agent_id(args),
        categories=args.get("categories"),
        source_app=_default_source_app(args),
        infer=False,
        context_messages=args.get("context"),
    )


def _handle_search_memory(memory, args):
    _maybe_sync_codex_runtime(args)
    try:
        limit = max(1, min(1000, int(args.get("limit", 10))))
    except (ValueError, TypeError):
        limit = 10
    orchestration_mode = str(args.get("orchestration_mode", "off") or "off").strip().lower()
    if orchestration_mode in {"hybrid", "strict"}:
        try:
            context_top_k = max(1, min(200, int(args.get("answer_context_top_k", 10))))
        except (ValueError, TypeError):
            context_top_k = 10
        result = memory.search_orchestrated(
            query=args.get("query", ""),
            user_id=_default_user_id(args),
            agent_id=args.get("agent_id"),
            repo=args.get("repo"),
            categories=args.get("categories"),
            limit=limit,
            question_type=args.get("question_type", ""),
            question_date=args.get("question_date", ""),
            orchestration_mode=orchestration_mode,
            base_search_limit=limit,
            base_context_limit=context_top_k,
            include_evidence=True,
            keyword_search=True,
            rerank=True,
        )
    else:
        result = memory.search(
            query=args.get("query", ""),
            user_id=_default_user_id(args),
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
                "memory_class": r.get("memory_class"),
                "memory_kind": r.get("canonical_kind") or r.get("memory_type"),
                "recall_explanation": r.get("recall_explanation"),
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
        user_id=_default_user_id(args),
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


def _handle_dhee_context(memory, args):
    """HyperAgent session bootstrap — Buddhi-powered.

    Returns performance trends, synthesized insights, relevant skills,
    pending intentions, proactive warnings, and top memories.
    """
    _maybe_sync_codex_runtime(args)
    user_id = args.get("user_id", "default")
    task_description = args.get("task_description")
    buddhi = get_buddhi()
    hyper_ctx = buddhi.get_hyper_context(
        user_id=user_id,
        task_description=task_description,
        memory=memory,
    )
    result = hyper_ctx.to_dict()
    try:
        from dhee.core.learnings import LearningExchange
        result["learnings"] = LearningExchange().search(
            query=task_description or "",
            repo=args.get("repo"),
            status="promoted",
            limit=max(1, min(10, int(args.get("limit", 5) or 5))),
        )
    except Exception:
        result["learnings"] = []
    return result


def _handle_dhee_scene_world_route(_memory, args):
    task = str(args.get("task") or args.get("query") or "")
    if not task.strip():
        return {"error": "task is required"}
    try:
        from dhee.hooks.scene_world import route_task

        return route_task(
            task,
            repo=args.get("repo"),
            user_id=_default_user_id(args),
            harness=str(args.get("harness") or os.environ.get("DHEE_HARNESS") or _default_agent_id(args)),
            top_k=_bounded_limit(args, "top_k", 4, 8),
            record=args.get("record") if "record" in args else None,
        )
    except Exception as exc:
        return {"enabled": False, "status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def _scene_evidence_from_args(memory, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    from dhee.temporal_scenes import collect_live_scene_sources, collect_scene_evidence

    sources = set(str(source) for source in (args.get("sources") or ["evidence"]))
    if args.get("include_recent_memories"):
        sources.add("memory")
    if args.get("include_repo_context"):
        sources.add("repo_context")
    if args.get("include_session"):
        sources.add("session")
    if args.get("include_shared_task_results"):
        sources.add("shared_task_results")
    if args.get("include_artifacts"):
        sources.add("artifacts")
    if args.get("include_live_sources"):
        sources.update({"session", "shared_task_results", "artifacts"})
    needs_live = bool(args.get("include_live_sources")) or any(
        source in sources
        for source in ("session", "session_digest", "shared_task_results", "shared_task", "artifacts", "artifact")
    )
    live: Dict[str, Any] = {}
    if needs_live:
        live_db = None
        if any(source in sources for source in ("shared_task_results", "shared_task", "artifacts", "artifact")):
            live_db = get_db()
        live = collect_live_scene_sources(
            db=live_db,
            repo=args.get("repo"),
            user_id=_default_user_id(args),
            agent_id=_default_agent_id(args),
            limit=_bounded_limit(args, "limit", 8, 50),
            include_session=("session" in sources or "session_digest" in sources) and not args.get("session"),
            include_shared_task_results=("shared_task_results" in sources or "shared_task" in sources) and not args.get("shared_task_results"),
            include_artifacts=("artifacts" in sources or "artifact" in sources) and not args.get("artifacts"),
        )
    mem = memory or (get_memory() if "memory" in sources else None)
    return collect_scene_evidence(
        evidence=args.get("evidence") or [],
        memory=mem,
        query=str(args.get("query") or args.get("task") or ""),
        user_id=_default_user_id(args),
        repo=args.get("repo"),
        session=args.get("session") or live.get("session"),
        shared_task_results=args.get("shared_task_results") or live.get("shared_task_results"),
        artifacts=args.get("artifacts") or live.get("artifacts"),
        sources=sources,
        limit=_bounded_limit(args, "limit", 8, 50),
    )


def _handle_dhee_scene_compile(memory, args):
    from dhee.temporal_scenes import compile_scene

    evidence = _scene_evidence_from_args(memory, args)
    if not evidence:
        return {"error": "evidence is required unless include_recent_memories returns results"}
    scene = compile_scene(
        evidence,
        user_id=_default_user_id(args),
        repo=args.get("repo"),
        task=str(args.get("task") or args.get("query") or ""),
        privacy_scope=str(args.get("privacy_scope") or "personal"),
        title=args.get("title"),
        store_dir=args.get("store_dir"),
        save=args.get("save") is not False,
    )
    return {
        "format": "dhee_scene_compile.v1",
        "scene": scene.to_dict(),
        "card": scene.to_card(),
    }


def _handle_dhee_scene_search(_memory, args):
    from dhee.temporal_scenes import search_scenes

    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    scenes = search_scenes(
        query,
        user_id=_default_user_id(args),
        repo=args.get("repo"),
        limit=_bounded_limit(args, "limit", 5, 30),
        store_dir=args.get("store_dir"),
        include_personal=args.get("include_personal") is not False,
    )
    return {
        "format": "dhee_scene_search.v1",
        "results": [scene.to_card() for scene in scenes],
    }


def _handle_dhee_context_pack(_memory, args):
    from dhee.temporal_scenes import build_context_pack

    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    try:
        budget = int(args.get("token_budget") or 1200)
    except (TypeError, ValueError):
        budget = 1200
    return build_context_pack(
        query,
        user_id=_default_user_id(args),
        repo=args.get("repo"),
        token_budget=max(128, min(20_000, budget)),
        limit=_bounded_limit(args, "limit", 5, 30),
        store_dir=args.get("store_dir"),
        include_personal=args.get("include_personal") is not False,
    )


def _repo_brain_goal(args: Dict[str, Any]) -> str:
    return str(args.get("goal") or args.get("query") or args.get("task") or "").strip()


def _handle_dhee_repo_brain_index(_memory, args):
    from dhee.repo_intelligence import build_repo_brain, localize_issue, repo_brain_summary

    goal = _repo_brain_goal(args)
    try:
        file_limit = max(100, min(20_000, int(args.get("file_limit") or 4_000)))
    except (TypeError, ValueError):
        file_limit = 4_000
    brain = build_repo_brain(
        args.get("repo"),
        goal=goal,
        relevant_files=args.get("relevant_files") if isinstance(args.get("relevant_files"), list) else None,
        must_run=args.get("must_run") if isinstance(args.get("must_run"), list) else None,
        file_limit=file_limit,
        persist=args.get("persist") is not False,
    )
    return {
        "format": "dhee_repo_brain_index.v1",
        "repo_intelligence": repo_brain_summary(brain),
        "localization": localize_issue(goal, brain) if goal else None,
    }


def _handle_dhee_repo_brain_get(_memory, args):
    from dhee.repo_intelligence import load_repo_brain, repo_brain_summary

    loaded = load_repo_brain(
        args.get("repo"),
        ref=args.get("ref"),
        quarantine=bool(args.get("quarantine") or False),
    )
    brain = loaded.get("brain") if isinstance(loaded.get("brain"), dict) else None
    if brain and not args.get("include_brain"):
        loaded["repo_intelligence"] = repo_brain_summary(brain)
        loaded["brain"] = None
    return loaded


def _handle_dhee_repo_brain_localize(_memory, args):
    from dhee.repo_intelligence import load_repo_brain, localize_issue, repo_brain_summary

    goal = _repo_brain_goal(args)
    if not goal:
        return {"error": "goal, query, or task is required"}
    loaded = load_repo_brain(
        args.get("repo"),
        ref=args.get("ref"),
        quarantine=bool(args.get("quarantine") or False),
    )
    brain = loaded.get("brain") if isinstance(loaded.get("brain"), dict) else None
    if not brain:
        return {
            "format": "dhee_repo_brain_localize.v1",
            "ok": False,
            "error": "repo brain not found; run dhee_repo_brain_index first",
            "diagnostics": loaded.get("diagnostics") or [],
        }
    try:
        limit = max(1, min(100, int(args.get("limit") or 12)))
    except (TypeError, ValueError):
        limit = 12
    return {
        "format": "dhee_repo_brain_localize.v1",
        "ok": True,
        "repo_intelligence": repo_brain_summary(brain),
        "localization": localize_issue(goal, brain, limit=limit),
    }


def _handle_dhee_repo_graph_export(_memory, args):
    from dhee.repo_intelligence import build_repo_brain, load_repo_brain, repo_graph_from_brain

    loaded = load_repo_brain(
        args.get("repo"),
        ref=args.get("ref"),
        quarantine=bool(args.get("quarantine") or False),
    )
    brain = loaded.get("brain") if isinstance(loaded.get("brain"), dict) else None
    if not brain:
        goal = _repo_brain_goal(args) or "repo graph export"
        brain = build_repo_brain(
            args.get("repo"),
            goal=goal,
            relevant_files=args.get("relevant_files"),
            must_run=args.get("must_run"),
            persist=bool(args.get("persist", True)),
        )
    graph = repo_graph_from_brain(
        brain,
        node_limit=int(args.get("node_limit") or args.get("limit") or 4000),
        edge_limit=int(args.get("edge_limit") or 12000),
    )
    compact = {
        "schema_version": graph.get("schema_version"),
        "artifact_id": graph.get("artifact_id"),
        "node_count": len(graph.get("nodes") or []),
        "edge_count": len(graph.get("edges") or []),
        "node_types": graph.get("node_types"),
        "edge_types": graph.get("edge_types"),
    }
    return {
        "format": "dhee_repo_graph_export.v1",
        "repo_graph": graph if bool(args.get("include_graph", True)) else compact,
    }


def _handle_dhee_context_graph_query(_memory, args):
    from dhee.repo_intelligence import build_repo_brain, context_graph_query, load_repo_brain

    query = str(args.get("query") or args.get("goal") or args.get("task") or "").strip()
    if not query:
        return {"error": "query is required"}
    loaded = load_repo_brain(
        args.get("repo"),
        ref=args.get("ref"),
        quarantine=bool(args.get("quarantine") or False),
    )
    brain = loaded.get("brain") if isinstance(loaded.get("brain"), dict) else None
    if not brain:
        brain = build_repo_brain(
            args.get("repo"),
            goal=query,
            relevant_files=args.get("relevant_files"),
            must_run=args.get("must_run"),
            persist=bool(args.get("persist", True)),
        )
    return {
        "format": "dhee_context_graph_query.v1",
        "context_graph": context_graph_query(
            brain,
            query,
            limit=int(args.get("limit") or 500),
            max_hops=int(args.get("max_hops") or 3),
        ),
    }


def _load_or_build_repo_brain(args, fallback_goal: str):
    from dhee.repo_intelligence import build_repo_brain, load_repo_brain

    loaded = load_repo_brain(
        args.get("repo"),
        ref=args.get("ref"),
        quarantine=bool(args.get("quarantine") or False),
    )
    brain = loaded.get("brain") if isinstance(loaded.get("brain"), dict) else None
    if brain:
        return brain
    return build_repo_brain(
        args.get("repo"),
        goal=fallback_goal,
        relevant_files=args.get("relevant_files"),
        must_run=args.get("must_run"),
        persist=bool(args.get("persist", True)),
    )


def _handle_dhee_repo_symbol_search(_memory, args):
    from dhee.repo_intelligence import repo_symbol_search

    query = str(args.get("query") or args.get("goal") or args.get("task") or "").strip()
    if not query:
        return {"error": "query is required"}
    brain = _load_or_build_repo_brain(args, query)
    return {
        "format": "dhee_repo_symbol_search.v1",
        "symbol_search": repo_symbol_search(
            brain,
            query,
            kind=args.get("kind"),
            language=args.get("language"),
            path=args.get("path"),
            limit=int(args.get("limit") or 20),
            include_tests=bool(args.get("include_tests") or False),
        ),
    }


def _handle_dhee_repo_callers(_memory, args):
    from dhee.repo_intelligence import repo_callers

    symbol = str(args.get("symbol") or args.get("query") or "").strip()
    if not symbol:
        return {"error": "symbol is required"}
    brain = _load_or_build_repo_brain(args, symbol)
    return {
        "format": "dhee_repo_callers.v1",
        "callers": repo_callers(
            brain,
            symbol,
            depth=int(args.get("depth") or 1),
            limit=int(args.get("limit") or 50),
        ),
    }


def _handle_dhee_repo_callees(_memory, args):
    from dhee.repo_intelligence import repo_callees

    symbol = str(args.get("symbol") or args.get("query") or "").strip()
    if not symbol:
        return {"error": "symbol is required"}
    brain = _load_or_build_repo_brain(args, symbol)
    return {
        "format": "dhee_repo_callees.v1",
        "callees": repo_callees(
            brain,
            symbol,
            depth=int(args.get("depth") or 1),
            limit=int(args.get("limit") or 50),
        ),
    }


def _handle_dhee_repo_impact(_memory, args):
    from dhee.repo_intelligence import repo_impact

    target = str(args.get("symbol_or_path") or args.get("symbol") or args.get("path") or args.get("query") or "").strip()
    if not target:
        return {"error": "symbol_or_path is required"}
    brain = _load_or_build_repo_brain(args, target)
    return {
        "format": "dhee_repo_impact.v1",
        "impact": repo_impact(
            brain,
            target,
            depth=int(args.get("depth") or 2),
            limit=int(args.get("limit") or 100),
            include_tests=bool(args.get("include_tests", True)),
        ),
    }


def _handle_dhee_repo_explore(_memory, args):
    from dhee.repo_intelligence import repo_explore

    query = str(args.get("query") or args.get("goal") or args.get("task") or "").strip()
    if not query:
        return {"error": "query is required"}
    brain = _load_or_build_repo_brain(args, query)
    return {
        "format": "dhee_repo_explore.v1",
        "explore": repo_explore(
            brain,
            query,
            max_hops=int(args.get("max_hops") or 3),
            max_files=int(args.get("max_files") or 8),
            max_symbols=int(args.get("max_symbols") or 40),
            max_source_chars=int(args.get("max_source_chars") or 18000),
        ),
    }


def _temporal_fact_ledger(args):
    from dhee.temporal_fact_ledger import open_default_ledger

    return open_default_ledger(args.get("db_path"))


def _handle_dhee_temporal_fact_assert(_memory, args):
    ledger = _temporal_fact_ledger(args)
    try:
        return ledger.assert_fact(
            fact_text=str(args.get("fact_text") or ""),
            user_id=str(args.get("user_id") or "default"),
            namespace=str(args.get("namespace") or "default"),
            subject=str(args.get("subject") or ""),
            predicate=str(args.get("predicate") or ""),
            object=str(args.get("object") or ""),
            valid_from=args.get("valid_from"),
            valid_to=args.get("valid_to"),
            observed_at=args.get("observed_at"),
            confidence=float(args.get("confidence") or 0.75),
            source_scene=str(args.get("source_scene") or ""),
            source_event_ids=args.get("source_event_ids") or [],
            source_memory_ids=args.get("source_memory_ids") or [],
            evidence=args.get("evidence") or [],
            privacy_scope=str(args.get("privacy_scope") or "personal"),
            metadata=args.get("metadata") or {},
            contradicts_fact_ids=args.get("contradicts_fact_ids") or [],
            invalidate_conflicts=bool(args.get("invalidate_conflicts", True)),
            actor_id=str(args.get("actor_id") or ""),
        )
    finally:
        ledger.close()


def _handle_dhee_temporal_fact_search(_memory, args):
    ledger = _temporal_fact_ledger(args)
    try:
        return ledger.search(
            str(args.get("query") or ""),
            user_id=str(args.get("user_id") or "default"),
            namespace=args.get("namespace"),
            active_only=bool(args.get("active_only", True)),
            as_of=args.get("as_of"),
            include_invalidated=bool(args.get("include_invalidated") or False),
            privacy_scope=args.get("privacy_scope"),
            limit=int(args.get("limit") or 20),
        )
    finally:
        ledger.close()


def _handle_dhee_temporal_fact_get(_memory, args):
    ledger = _temporal_fact_ledger(args)
    try:
        fact_id = str(args.get("fact_id") or args.get("id") or "")
        if not fact_id:
            return {"error": "fact_id is required"}
        fact = ledger.get_fact(
            fact_id,
            user_id=str(args.get("user_id") or "default") if args.get("user_id") else None,
            include_events=bool(args.get("include_events") or False),
        )
        return {"format": "dhee_temporal_fact_get.v1", "ok": bool(fact), "fact": fact}
    finally:
        ledger.close()


def _handle_dhee_temporal_fact_invalidate(_memory, args):
    ledger = _temporal_fact_ledger(args)
    try:
        fact_id = str(args.get("fact_id") or args.get("id") or "")
        if not fact_id:
            return {"error": "fact_id is required"}
        return ledger.invalidate_fact(
            fact_id,
            user_id=str(args.get("user_id") or "default"),
            reason=str(args.get("reason") or "invalidated"),
            contradicted_by=args.get("contradicted_by"),
            invalidated_at=args.get("invalidated_at"),
            actor_id=str(args.get("actor_id") or ""),
        )
    finally:
        ledger.close()


def _handle_dhee_temporal_fact_stats(_memory, args):
    ledger = _temporal_fact_ledger(args)
    try:
        return ledger.stats(user_id=str(args.get("user_id") or "default"), namespace=args.get("namespace"))
    finally:
        ledger.close()


def _handle_dhee_task_contract_compile(_memory, args):
    from dhee.task_contracts import compile_task_contract

    goal = str(args.get("goal") or args.get("task") or args.get("query") or "").strip()
    if not goal:
        return {"error": "goal, task, or query is required"}
    return compile_task_contract(
        goal,
        repo=args.get("repo"),
        mode=str(args.get("mode") or "patch"),
        risk=args.get("risk"),
        allowed_write_paths=args.get("allowed_write_paths"),
        forbidden_paths=args.get("forbidden_paths"),
        must_run=args.get("must_run"),
        success_criteria=args.get("success_criteria"),
        context_budget=args.get("context_budget"),
        memory_pointers=args.get("memory_pointers"),
        recent_failures=args.get("recent_failures"),
    )


def _task_goal_from_args(args: Dict[str, Any]) -> str:
    return str(args.get("goal") or args.get("task") or args.get("query") or "").strip()


def _handle_dhee_task_contract_create(_memory, args):
    from dhee.task_contracts import create_task_contract

    goal = _task_goal_from_args(args)
    if not goal:
        return {"error": "goal, task, or query is required"}
    return create_task_contract(
        goal,
        repo=args.get("repo"),
        out=args.get("out"),
        mode=str(args.get("mode") or "patch"),
        risk=args.get("risk"),
        allowed_write_paths=args.get("allowed_write_paths"),
        forbidden_paths=args.get("forbidden_paths"),
        must_run=args.get("must_run"),
        success_criteria=args.get("success_criteria"),
        context_budget=args.get("context_budget"),
        memory_pointers=args.get("memory_pointers"),
        recent_failures=args.get("recent_failures"),
    )


def _handle_dhee_task_contract_list(_memory, args):
    from dhee.task_contracts import list_task_contracts

    return {
        "format": "dhee_task_contract_list.v1",
        "results": list_task_contracts(repo=args.get("repo")),
    }


def _handle_dhee_task_contract_get(_memory, args):
    from dhee.task_contracts import get_task_contract

    task_id = str(args.get("task_id") or args.get("id") or "")
    if not task_id:
        return {"error": "task_id is required"}
    return get_task_contract(task_id, repo=args.get("repo"))


def _handle_dhee_task_contract_import(_memory, args):
    from dhee.task_contracts import import_task_contract

    path = str(args.get("path") or "")
    if not path:
        return {"error": "path is required"}
    return import_task_contract(path, repo=args.get("repo"))


def _handle_dhee_task_contract_interpret(_memory, args):
    from dhee.task_contracts import interpret_task_contract

    task_contract = args.get("contract") or args.get("path") or args.get("task_id") or args.get("id")
    if not task_contract:
        return {"error": "contract, path, or task_id is required"}
    return interpret_task_contract(
        task_contract,
        repo=args.get("repo"),
        strict=bool(args.get("strict") or False),
    )


def _contract_ref_from_args(args: Dict[str, Any]) -> Any:
    return args.get("contract") or args.get("path") or args.get("task_id") or args.get("id")


def _handle_dhee_contract_supervise_action(_memory, args):
    from dhee.contract_supervisor import supervise_action

    task_contract = _contract_ref_from_args(args)
    action = args.get("action") or args.get("proposed_action")
    if not task_contract:
        return {"error": "contract, path, or task_id is required"}
    if not isinstance(action, dict):
        return {"error": "action or proposed_action object is required"}
    return supervise_action(
        task_contract,
        action,
        repo=args.get("repo"),
        strict=bool(args.get("strict") or False),
    )


def _handle_dhee_contract_record_observation(_memory, args):
    from dhee.contract_supervisor import record_observation_transition

    task_contract = _contract_ref_from_args(args)
    action = args.get("action")
    if not task_contract:
        return {"error": "contract, path, or task_id is required"}
    if not isinstance(action, dict):
        return {"error": "action object is required"}
    return record_observation_transition(
        task_contract,
        action,
        args.get("observation") or "",
        repo=args.get("repo"),
        outcome=str(args.get("outcome") or "observed"),
        next_action=args.get("next_action") if isinstance(args.get("next_action"), dict) else None,
        strict=bool(args.get("strict") or False),
    )


def _handle_dhee_contract_proof_bundle(_memory, args):
    from dhee.contract_supervisor import build_proof_bundle

    task_contract = _contract_ref_from_args(args)
    if not task_contract:
        return {"error": "contract, path, or task_id is required"}
    persist = args.get("persist")
    return build_proof_bundle(
        task_contract,
        repo=args.get("repo"),
        strict=bool(args.get("strict") or False),
        persist=True if persist is None else bool(persist),
    )


def _handle_dhee_contract_run_verification(_memory, args):
    from dhee.verification_runner import run_verification

    task_contract = _contract_ref_from_args(args)
    if not task_contract:
        return {"error": "contract, path, or task_id is required"}
    persist = args.get("persist")
    return run_verification(
        task_contract,
        repo=args.get("repo"),
        timeout_sec=int(args.get("timeout_sec") or 120),
        max_commands=int(args.get("max_commands") or 24),
        include_pass_to_pass=True if args.get("include_pass_to_pass") is None else bool(args.get("include_pass_to_pass")),
        include_static=True if args.get("include_static") is None else bool(args.get("include_static")),
        include_security=True if args.get("include_security") is None else bool(args.get("include_security")),
        strict=bool(args.get("strict") or False),
        persist=True if persist is None else bool(persist),
    )


def _handle_dhee_contract_runtime_activate(_memory, args):
    from dhee.contract_runtime import activate_contract_runtime

    task_contract = _contract_ref_from_args(args)
    if not task_contract:
        return {"error": "contract, path, or task_id is required"}
    return activate_contract_runtime(
        task_contract,
        repo=args.get("repo"),
        strict=bool(args.get("strict") or False),
        force=bool(args.get("force") or False),
        agent_id=args.get("agent_id"),
        harness=args.get("harness"),
    )


def _handle_dhee_contract_runtime_status(_memory, args):
    from dhee.contract_runtime import contract_runtime_status

    return contract_runtime_status(repo=args.get("repo"))


def _handle_dhee_contract_runtime_deactivate(_memory, args):
    from dhee.contract_runtime import deactivate_contract_runtime

    return deactivate_contract_runtime(
        repo=args.get("repo"),
        agent_id=args.get("agent_id"),
        reason=str(args.get("reason") or "manual"),
    )


def _handle_dhee_contract_enforcement_set(_memory, args):
    from dhee.contract_runtime import set_contract_enforcement

    return set_contract_enforcement(
        str(args.get("mode") or ""),
        repo=args.get("repo"),
        agent_id=args.get("agent_id"),
        reason=args.get("reason"),
    )


def _handle_dhee_contract_enforcement_status(_memory, args):
    from dhee.contract_runtime import contract_enforcement_status

    return contract_enforcement_status(repo=args.get("repo"))


def _handle_dhee_contract_runtime_doctor(_memory, args):
    from dhee.contract_runtime import contract_runtime_doctor

    return contract_runtime_doctor(repo=args.get("repo"))


def _handle_dhee_update_capsule_create(_memory, args):
    from dhee.update_capsules import create_update_capsule

    return create_update_capsule(
        repo=args.get("repo"),
        since=args.get("since"),
        task_id=args.get("task_id"),
        out=args.get("out"),
        title=args.get("title"),
        summary=args.get("summary"),
        commands=args.get("commands"),
        evidence=args.get("evidence"),
    )


def _handle_dhee_update_capsule_list(_memory, args):
    from dhee.update_capsules import list_update_capsules

    return {
        "format": "dhee_update_capsule_list.v1",
        "results": list_update_capsules(repo=args.get("repo")),
    }


def _handle_dhee_update_capsule_get(_memory, args):
    from dhee.update_capsules import get_update_capsule

    capsule_id = str(args.get("capsule_id") or args.get("id") or "")
    if not capsule_id:
        return {"error": "capsule_id is required"}
    return get_update_capsule(capsule_id, repo=args.get("repo"))


def _handle_dhee_update_capsule_import(_memory, args):
    from dhee.update_capsules import import_update_capsule

    path = str(args.get("path") or "")
    if not path:
        return {"error": "path is required"}
    return import_update_capsule(
        path,
        repo=args.get("repo"),
        allow_private=bool(args.get("allow_private") or False),
    )


def _handle_dhee_update_capsule_interpret(_memory, args):
    from dhee.update_capsules import interpret_update_capsule

    capsule = args.get("capsule") or args.get("path") or args.get("capsule_id") or args.get("id")
    if not capsule:
        return {"error": "capsule, path, or capsule_id is required"}
    return interpret_update_capsule(
        capsule,
        repo=args.get("repo"),
        strict=bool(args.get("strict") or False),
    )


def _handle_get_last_session(_memory, args):
    from dhee.core.kernel import get_last_session
    session = get_last_session(
        agent_id=args.get("agent_id"),
        repo=args.get("repo"),
        user_id=_default_user_id(args),
        requester_agent_id=_default_requester_agent_id(args),
        fallback_log_recovery=args.get("fallback_log_recovery", True),
    )
    if session is None:
        return {"status": "no_session", "message": "No previous session found."}
    return session


def _handle_save_session_digest(_memory, args):
    from dhee.core.kernel import save_session_digest
    return save_session_digest(
        task_summary=args.get("task_summary", ""),
        agent_id=_default_agent_id(args),
        requester_agent_id=_default_requester_agent_id(args),
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
        user_id=args.get("user_id") or os.environ.get("DHEE_USER_ID"),
        agent_id=args.get("agent_id"),
    )


def _handle_audit_memory_quality(memory, args):
    try:
        limit = max(1, min(100_000, int(args.get("limit", 10_000))))
    except (TypeError, ValueError):
        limit = 10_000
    return memory.audit_memory_quality(
        user_id=args.get("user_id") or os.environ.get("DHEE_USER_ID"),
        agent_id=args.get("agent_id"),
        limit=limit,
        profile_keyword=args.get("profile_keyword"),
        require_personal_model=bool(args.get("require_personal_model", True)),
    )


def _handle_repair_memory_quality(memory, args):
    try:
        limit = max(1, min(100_000, int(args.get("limit", 10_000))))
    except (TypeError, ValueError):
        limit = 10_000
    return memory.repair_memory_quality(
        user_id=args.get("user_id") or os.environ.get("DHEE_USER_ID"),
        agent_id=args.get("agent_id"),
        limit=limit,
        dry_run=bool(args.get("dry_run", True)),
        reindex_vectors=bool(args.get("reindex_vectors", False)),
    )


def _handle_search_skills(memory, args):
    try:
        limit = max(1, min(50, int(args.get("limit", 5))))
    except (ValueError, TypeError):
        limit = 5
    min_conf = float(args.get("min_confidence", 0.0))
    return memory.search_skills(
        query=args.get("query", ""),
        limit=limit,
        tags=args.get("tags"),
        min_confidence=min_conf,
    )


def _handle_apply_skill(memory, args):
    return memory.apply_skill(
        skill_id=args.get("skill_id", ""),
    )


def _handle_log_skill_outcome(memory, args):
    return memory.log_skill_outcome(
        skill_id=args.get("skill_id", ""),
        success=args.get("success", False),
        notes=args.get("notes"),
        step_outcomes=args.get("step_outcomes"),
    )


def _handle_record_trajectory_step(memory, args):
    return memory.record_trajectory_step(
        recorder_id=args.get("recorder_id", ""),
        action=args.get("action", ""),
        tool=args.get("tool", ""),
        args=args.get("args"),
        result_summary=args.get("result_summary", ""),
        error=args.get("error"),
    )


def _handle_mine_skills(memory, args):
    action = args.get("action", "mine")
    if action == "start_trajectory":
        recorder_id = memory.start_trajectory(
            task_description=args.get("task_description", ""),
        )
        return {"recorder_id": recorder_id}
    elif action == "complete_trajectory":
        return memory.complete_trajectory(
            recorder_id=args.get("recorder_id", ""),
            success=args.get("success", False),
            outcome_summary=args.get("outcome_summary", ""),
        )
    else:
        return memory.mine_skills(
            task_query=args.get("task_query"),
        )


def _handle_get_skill_stats(memory, args):
    return memory.get_skill_stats()


def _handle_search_skills_structural(memory, args):
    query_steps = args.get("query_steps", [])
    try:
        limit = max(1, min(50, int(args.get("limit", 5))))
    except (ValueError, TypeError):
        limit = 5
    min_sim = float(args.get("min_similarity", 0.3))
    return memory.search_skills_structural(
        query_steps=query_steps,
        limit=limit,
        min_similarity=min_sim,
    )


def _handle_analyze_skill_gaps(memory, args):
    return memory.analyze_skill_gaps(
        skill_id=args.get("skill_id", ""),
        target_context=args.get("target_context", {}),
    )


def _handle_decompose_skill(memory, args):
    return memory.decompose_skill(
        skill_id=args.get("skill_id", ""),
    )


def _handle_apply_skill_with_bindings(memory, args):
    return memory.apply_skill(
        skill_id=args.get("skill_id", ""),
        bindings=args.get("bindings", {}),
    )


def _handle_enrich_pending(memory, args):
    try:
        batch_size = max(1, min(50, int(args.get("batch_size", 10))))
    except (ValueError, TypeError):
        batch_size = 10
    try:
        max_batches = max(1, min(100, int(args.get("max_batches", 5))))
    except (ValueError, TypeError):
        max_batches = 5
    return memory.enrich_pending(
        batch_size=batch_size,
        max_batches=max_batches,
    )


def _handle_think(memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle the think tool — cognitive decomposition loop."""
    question = arguments.get("question", "")
    user_id = arguments.get("user_id", "default")
    max_depth = arguments.get("max_depth", 3)

    if not question:
        return {"error": "question is required"}

    if hasattr(memory, "think"):
        result = memory.think(
            question=question,
            user_id=_default_user_id(arguments) if not arguments.get("user_id") else user_id,
            max_depth=max_depth,
            repo=arguments.get("repo"),
        )
        if hasattr(result, "to_dict"):
            return result.to_dict()
        return result
    return {"error": "Cognition engine not available. Ensure cognition is enabled in config."}


def _handle_anticipate(memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Proactive intelligence — Buddhi checks intentions, insights, and scenes."""
    user_id = _default_user_id(arguments)
    context = arguments.get("context")

    buddhi = get_buddhi()
    result: Dict[str, Any] = {}

    # 1. Check triggered intentions
    triggered = buddhi._check_intentions(user_id, context)
    if triggered:
        result["triggered_intentions"] = [i.to_dict() for i in triggered]

    # 2. Relevant insights for current context
    insights = buddhi._get_relevant_insights(user_id, context)
    if insights:
        result["relevant_insights"] = [i.to_dict() for i in insights[:5]]

    # 3. Proactive warnings from performance data
    performance = buddhi._get_performance_snapshots(user_id, context)
    warnings = buddhi._generate_warnings(performance, insights)
    if warnings:
        result["warnings"] = warnings

    # 4. Prospective scenes (legacy support)
    if hasattr(memory, "get_prospective_scenes"):
        scenes = memory.get_prospective_scenes(user_id=user_id)
        if scenes:
            result["upcoming_scenes"] = scenes

    result["buddhi_stats"] = buddhi.get_stats()
    return result


def _handle_record_outcome(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Record task outcome for performance tracking."""
    task_type = arguments.get("task_type", "")
    score = float(arguments.get("score", 0.0))
    user_id = _default_user_id(arguments)
    metadata = arguments.get("metadata")

    if not task_type:
        return {"error": "task_type is required"}

    buddhi = get_buddhi()
    insight = buddhi.record_outcome(
        user_id=user_id,
        task_type=task_type,
        score=max(0.0, min(1.0, score)),
        metadata=metadata,
    )
    # Feed the same structured outcome into EvolutionLayer/MetaBuddhi so
    # strategy learning sees richer task-level signals, not just answer events.
    try:
        mem = get_memory_instance()
        evo = getattr(mem, "evolution_layer", None) if mem else None
        if evo is not None:
            evo.record_task_outcome(
                task_type=task_type,
                outcome_score=score,
                metadata=metadata if isinstance(metadata, dict) else {},
                source="mcp_record_outcome",
            )
    except Exception:
        pass
    result: Dict[str, Any] = {"recorded": True, "task_type": task_type, "score": score}
    if insight:
        result["auto_insight"] = insight.to_dict()
    return result


def _handle_reflect(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Agent-triggered reflection — synthesize insights from experience."""
    task_type = arguments.get("task_type", "")
    user_id = _default_user_id(arguments)

    if not task_type:
        return {"error": "task_type is required"}

    buddhi = get_buddhi()
    new_insights = buddhi.reflect(
        user_id=user_id,
        task_type=task_type,
        what_worked=arguments.get("what_worked"),
        what_failed=arguments.get("what_failed"),
        key_decision=arguments.get("key_decision"),
    )
    return {
        "insights_created": len(new_insights),
        "insights": [i.to_dict() for i in new_insights],
    }


def _handle_store_intention(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Store a future trigger — prospective memory."""
    description = arguments.get("description", "")
    user_id = _default_user_id(arguments)

    if not description:
        return {"error": "description is required"}

    buddhi = get_buddhi()
    intention = buddhi.store_intention(
        user_id=user_id,
        description=description,
        trigger_keywords=arguments.get("trigger_keywords"),
        trigger_after=arguments.get("trigger_after"),
        action_type=arguments.get("action_type", "remind"),
    )
    return {"stored": True, "intention": intention.to_dict()}


def _handle_dhee_submit_learning(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.learnings import LearningExchange

    exchange = LearningExchange()
    candidate = exchange.submit(
        title=str(arguments.get("title") or ""),
        body=str(arguments.get("body") or ""),
        kind=str(arguments.get("kind") or "heuristic"),
        source_agent_id=str(arguments.get("source_agent_id") or _default_agent_id(arguments)),
        source_harness=str(arguments.get("source_harness") or os.environ.get("DHEE_HARNESS") or "mcp"),
        task_type=arguments.get("task_type"),
        repo=arguments.get("repo"),
        scope=str(arguments.get("scope") or "personal"),
        confidence=float(arguments.get("confidence", 0.5) or 0.5),
        utility=float(arguments.get("utility", 0.0) or 0.0),
        evidence=arguments.get("evidence") or [],
        metadata={"user_id": _default_user_id(arguments)},
    )
    return {"learning": candidate.to_dict()}


def _handle_dhee_search_learnings(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.learnings import LearningExchange

    rows = LearningExchange().search(
        query=arguments.get("query") or "",
        task_type=arguments.get("task_type"),
        repo=arguments.get("repo"),
        status=str(arguments.get("status") or "promoted"),
        include_candidates=bool(arguments.get("include_candidates", False)),
        limit=_bounded_limit(arguments, "limit", 10, 50),
    )
    return {"count": len(rows), "results": rows}


def _handle_dhee_promote_learning(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.learnings import LearningExchange

    candidate = LearningExchange().promote(
        str(arguments.get("learning_id") or ""),
        scope=str(arguments.get("scope") or "personal"),
        repo=arguments.get("repo"),
        approved_by=arguments.get("approved_by"),
    )
    return {"learning": candidate.to_dict()}


def _handle_dhee_shell(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    repo = arguments.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    from dhee import runtime

    runtime_result = runtime.execute_shell(
        str(arguments.get("command") or ""),
        repo=repo,
        user_id=_default_user_id(arguments),
        agent_id=_default_agent_id(arguments),
        workspace_id=arguments.get("workspace_id") or repo,
    )
    if runtime_result is not None:
        return runtime_result

    from dhee.fs import ContextWorkspace

    workspace = ContextWorkspace(
        repo=repo,
        user_id=_default_user_id(arguments),
        agent_id=_default_agent_id(arguments),
        db=get_db(),
        workspace_id=arguments.get("workspace_id") or repo,
    )
    return workspace.execute(str(arguments.get("command") or "")).to_dict()


def _context_store(arguments: Dict[str, Any]):
    from dhee.context_state import ContextStateStore

    repo = arguments.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    return ContextStateStore(
        repo=repo,
        workspace_id=arguments.get("workspace_id") or repo,
        user_id=_default_user_id(arguments),
        agent_id=_default_agent_id(arguments),
    )


def _runtime_context(arguments: Dict[str, Any], action: str, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    from dhee import runtime

    repo = arguments.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    return runtime.execute_context(
        action,
        repo=repo,
        workspace_id=arguments.get("workspace_id") or repo,
        user_id=_default_user_id(arguments),
        agent_id=_default_agent_id(arguments),
        args=extra or {},
    )


def _handle_dhee_context_status(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    return _runtime_context(arguments, "status") or _context_store(arguments).status()


def _handle_dhee_context_state(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    runtime_result = _runtime_context(arguments, "state", {"format": str(arguments.get("format") or "card").lower()})
    if runtime_result is not None:
        return runtime_result
    store = _context_store(arguments)
    fmt = str(arguments.get("format") or "card").lower()
    if fmt == "json":
        return {"format": "dhee_context_state", "state": store.load(), "status": store.status()}
    if fmt == "markdown":
        return {"format": "markdown", "text": store.render_markdown()}
    return {"format": "card", "text": store.render_state_card(), "status": store.status()}


def _handle_dhee_context_checkpoint(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    reason = str(arguments.get("reason") or "mcp checkpoint")
    return _runtime_context(arguments, "checkpoint", {"reason": reason}) or _context_store(arguments).checkpoint(reason=reason)


def _handle_dhee_context_rollover(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    reason = str(arguments.get("reason") or "mcp rollover")
    return _runtime_context(arguments, "rollover", {"reason": reason}) or _context_store(arguments).rollover(reason=reason)


def _handle_dhee_context_provision(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    task = str(arguments.get("task") or arguments.get("query") or "")
    return _runtime_context(arguments, "provision", {"task": task}) or _context_store(arguments).provision(task)


def _handle_dhee_tools_list(_memory, _arguments: Dict[str, Any]) -> Dict[str, Any]:
    default_tools = [
        "remember",
        "recall",
        "dhee_context_status",
        "dhee_context_state",
        "dhee_context_checkpoint",
        "dhee_context_rollover",
        "dhee_context_provision",
        "dhee_scene_world_route",
        "dhee_scene_compile",
        "dhee_scene_search",
        "dhee_context_pack",
        "dhee_repo_brain_index",
        "dhee_repo_brain_get",
        "dhee_repo_brain_localize",
        "dhee_task_contract_compile",
        "dhee_task_contract_create",
        "dhee_task_contract_list",
        "dhee_task_contract_get",
        "dhee_task_contract_import",
        "dhee_task_contract_interpret",
        "dhee_contract_supervise_action",
        "dhee_contract_record_observation",
        "dhee_update_capsule_create",
        "dhee_update_capsule_list",
        "dhee_update_capsule_get",
        "dhee_update_capsule_import",
        "dhee_update_capsule_interpret",
        "dhee_shell",
        "dhee_read",
        "dhee_grep",
        "dhee_bash",
        "dhee_agent",
        "dhee_expand_result",
        "dhee_context_bootstrap",
        "dhee_inbox",
        "dhee_broadcast",
        "dhee_handoff",
    ]
    return {
        "format": "dhee_tools",
        "default_server": "dhee-mcp",
        "advanced_server": "dhee-mcp-full",
        "default_tools": default_tools,
        "advanced_tools": [tool.name for tool in TOOLS if tool.name not in default_tools],
        "note": "Use compiled-state and router tools first; full MCP is for administration and manual inspection.",
    }


def _handle_dhee_list_assets(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    db = get_db()
    try:
        limit = max(1, min(200, int(arguments.get("limit", 20))))
    except (ValueError, TypeError):
        limit = 20

    rows = db.list_artifacts(
        user_id=_default_user_id(arguments),
        workspace_id=arguments.get("workspace_id"),
        folder_path=arguments.get("folder_path"),
        limit=limit,
    )
    return {
        "results": [
            {
                "artifact_id": row.get("artifact_id"),
                "filename": row.get("filename"),
                "mime_type": row.get("mime_type"),
                "byte_size": row.get("byte_size"),
                "lifecycle_state": row.get("lifecycle_state"),
                "binding_count": row.get("binding_count", 0),
                "extraction_count": row.get("extraction_count", 0),
                "last_extraction_at": row.get("last_extraction_at"),
                "content_hash": row.get("content_hash"),
            }
            for row in rows
        ],
        "count": len(rows),
    }


def _handle_dhee_get_asset(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    db = get_db()
    artifact_id = str(arguments.get("artifact_id") or "").strip()
    if not artifact_id:
        source_path = str(arguments.get("source_path") or "").strip()
        if not source_path:
            return {"error": "artifact_id or source_path is required"}
        artifact = db.find_artifact_by_source_path(
            source_path,
            user_id=_default_user_id(arguments),
            workspace_id=arguments.get("workspace_id"),
        )
        if artifact is None:
            return {"error": "Artifact not found"}
        artifact_id = str(artifact.get("artifact_id") or "")

    artifact = db.get_artifact(artifact_id)
    if artifact is None:
        return {"error": "Artifact not found"}

    include_extraction_text = bool(arguments.get("include_extraction_text", False))
    include_chunks = bool(arguments.get("include_chunks", False))
    try:
        chunk_limit = max(1, min(50, int(arguments.get("chunk_limit", 5))))
    except (ValueError, TypeError):
        chunk_limit = 5
    try:
        max_text_chars = max(100, min(12000, int(arguments.get("max_text_chars", 1200))))
    except (ValueError, TypeError):
        max_text_chars = 1200

    extractions = []
    for row in artifact.get("extractions", []) or []:
        item = {
            "id": row.get("id"),
            "extraction_source": row.get("extraction_source"),
            "extraction_version": row.get("extraction_version"),
            "extraction_timestamp": row.get("extraction_timestamp"),
            "extracted_text_hash": row.get("extracted_text_hash"),
            "metadata": row.get("metadata", {}),
        }
        if include_extraction_text:
            item["extracted_text"] = str(row.get("extracted_text", ""))[:max_text_chars]
        extractions.append(item)

    chunks = []
    if include_chunks:
        for row in (artifact.get("chunks", []) or [])[:chunk_limit]:
            chunks.append(
                {
                    "id": row.get("id"),
                    "chunk_index": row.get("chunk_index"),
                    "start_offset": row.get("start_offset"),
                    "end_offset": row.get("end_offset"),
                    "content_hash": row.get("content_hash"),
                    "metadata": row.get("metadata", {}),
                    "content": str(row.get("content", ""))[:max_text_chars],
                }
            )

    return {
        "artifact_id": artifact.get("artifact_id"),
        "filename": artifact.get("filename"),
        "mime_type": artifact.get("mime_type"),
        "byte_size": artifact.get("byte_size"),
        "content_hash": artifact.get("content_hash"),
        "lifecycle_state": artifact.get("lifecycle_state"),
        "attached_at": artifact.get("attached_at"),
        "parsed_at": artifact.get("parsed_at"),
        "indexed_at": artifact.get("indexed_at"),
        "portable_at": artifact.get("portable_at"),
        "bindings": artifact.get("bindings", []),
        "extractions": extractions,
        "chunk_count": len(artifact.get("chunks", []) or []),
        "chunks": chunks,
    }


def _handle_dhee_sync_codex_artifacts(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.artifacts import ArtifactManager
    from dhee.core.codex_stream import sync_latest_codex_stream

    stats = sync_latest_codex_stream(
        ArtifactManager(get_db()),
        get_db(),
        user_id=_default_user_id(arguments),
        sessions_root=os.environ.get("DHEE_CODEX_SESSIONS_ROOT"),
        log_path=str(arguments.get("log_path") or "").strip() or None,
    )
    if stats.get("status") in {"no_log", "missing_log"}:
        return {"error": "No Codex session log found", **stats}
    return stats


def _handle_dhee_why(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    from dhee.core.provenance import explain_identifier

    try:
        history_limit = max(1, min(50, int(arguments.get("history_limit", 10))))
    except (ValueError, TypeError):
        history_limit = 10
    try:
        chunk_limit = max(1, min(50, int(arguments.get("chunk_limit", 5))))
    except (ValueError, TypeError):
        chunk_limit = 5
    try:
        max_text_chars = max(100, min(12000, int(arguments.get("max_text_chars", 1200))))
    except (ValueError, TypeError):
        max_text_chars = 1200

    return explain_identifier(
        get_db(),
        str(arguments.get("identifier") or ""),
        history_limit=history_limit,
        include_extraction_text=bool(arguments.get("include_extraction_text", False)),
        include_chunks=bool(arguments.get("include_chunks", False)),
        chunk_limit=chunk_limit,
        max_text_chars=max_text_chars,
    )


def _handle_dhee_handoff(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    from dhee.core.handoff_snapshot import build_handoff_snapshot

    def _bounded_int(name: str, default: int) -> int:
        try:
            return max(1, min(20, int(arguments.get(name, default))))
        except (ValueError, TypeError):
            return default

    repo = arguments.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))

    return build_handoff_snapshot(
        get_db(),
        user_id=_default_user_id(arguments),
        repo=repo,
        workspace_id=repo,
        thread_id=str(arguments.get("thread_id") or "").strip() or None,
        memory_limit=_bounded_int("memory_limit", 5),
        artifact_limit=_bounded_int("artifact_limit", 5),
        task_limit=_bounded_int("task_limit", 5),
        intention_limit=_bounded_int("intention_limit", 5),
    )


def _handle_dhee_context_bootstrap(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.context_bootstrap import build_context_bootstrap

    return build_context_bootstrap(
        get_db(),
        arguments,
        default_user_id=_default_user_id(arguments),
        default_agent_id=_default_agent_id(arguments),
    )


def _handle_dhee_thread_state(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    db = get_db()
    user_id = _default_user_id(arguments)
    thread_id = str(arguments.get("thread_id") or "").strip()
    if not thread_id:
        return {"error": "thread_id is required"}

    if bool(arguments.get("clear")):
        deleted = db.delete_thread_state(user_id=user_id, thread_id=thread_id)
        return {"thread_id": thread_id, "deleted": bool(deleted)}

    metadata = arguments.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return {"error": "metadata must be an object"}

    repo = arguments.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))

    update_keys = (
        "repo",
        "workspace_id",
        "folder_path",
        "status",
        "summary",
        "current_goal",
        "current_step",
        "session_id",
        "handoff_session_id",
        "metadata",
    )
    should_update = any(arguments.get(key) is not None for key in update_keys)
    if not should_update:
        state = db.get_thread_state(user_id=user_id, thread_id=thread_id)
        if state is None:
            return {"status": "not_found", "thread_id": thread_id}
        return state

    return db.upsert_thread_state(
        {
            "user_id": user_id,
            "thread_id": thread_id,
            "repo": repo,
            "workspace_id": arguments.get("workspace_id") or repo,
            "folder_path": arguments.get("folder_path"),
            "status": arguments.get("status") or "active",
            "summary": arguments.get("summary"),
            "current_goal": arguments.get("current_goal"),
            "current_step": arguments.get("current_step"),
            "session_id": arguments.get("session_id"),
            "handoff_session_id": arguments.get("handoff_session_id"),
            "metadata": metadata or {},
        }
    )


def _handle_dhee_shared_task(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    from dhee.core.shared_tasks import resolve_active_shared_task

    db = get_db()
    user_id = _default_user_id(arguments)
    action = str(arguments.get("action") or "show").strip().lower()
    repo = arguments.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    metadata = arguments.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return {"error": "metadata must be an object"}

    if action == "create":
        title = str(arguments.get("title") or "").strip()
        if not title:
            return {"error": "title is required for action=create"}
        task = db.upsert_shared_task(
            {
                "id": arguments.get("shared_task_id"),
                "user_id": user_id,
                "repo": repo or os.getcwd(),
                "workspace_id": arguments.get("workspace_id") or repo or os.getcwd(),
                "folder_path": arguments.get("folder_path"),
                "title": title,
                "status": "active",
                "created_by": _default_agent_id(arguments),
                "metadata": metadata or {},
            }
        )
        return task

    if action == "list":
        try:
            limit = max(1, min(100, int(arguments.get("limit", 20))))
        except (TypeError, ValueError):
            limit = 20
        rows = db.list_shared_tasks(user_id=user_id, repo=repo, limit=limit)
        return {"count": len(rows), "results": rows}

    task = resolve_active_shared_task(
        db,
        user_id=user_id,
        shared_task_id=str(arguments.get("shared_task_id") or "").strip() or None,
        repo=repo,
        cwd=repo,
    )
    if not task:
        return {"status": "not_found"}

    if action == "close":
        keep_results = bool(arguments.get("keep_results"))
        closed = db.close_shared_task(
            str(task["id"]),
            user_id=user_id,
            status="completed",
            prune_results=not keep_results,
        )
        return {
            "shared_task_id": task["id"],
            "closed": bool(closed),
            "kept_results": keep_results,
        }

    return task


def _handle_dhee_shared_task_results(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    from dhee.core.shared_tasks import resolve_active_shared_task

    db = get_db()
    user_id = _default_user_id(arguments)
    repo = arguments.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    task = resolve_active_shared_task(
        db,
        user_id=user_id,
        shared_task_id=str(arguments.get("shared_task_id") or "").strip() or None,
        repo=repo,
        cwd=repo,
    )
    if not task:
        return {"status": "not_found", "results": []}
    try:
        limit = max(1, min(100, int(arguments.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10
    rows = db.list_shared_task_results(
        shared_task_id=str(task["id"]),
        limit=limit,
        result_status=arguments.get("result_status"),
        packet_kind=arguments.get("packet_kind"),
    )
    compact = []
    for row in rows:
        compact.append(
            {
                "id": row.get("id"),
                "packet_kind": row.get("packet_kind"),
                "tool_name": row.get("tool_name"),
                "result_status": row.get("result_status"),
                "source_path": row.get("source_path"),
                "ptr": row.get("ptr"),
                "artifact_id": row.get("artifact_id"),
                "digest": row.get("digest"),
                "harness": row.get("harness"),
                "agent_id": row.get("agent_id"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            }
        )
    return {
        "shared_task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "status": task.get("status"),
            "repo": task.get("repo"),
            "workspace_id": task.get("workspace_id"),
            "folder_path": task.get("folder_path"),
            "updated_at": task.get("updated_at"),
        },
        "count": len(compact),
        "results": compact,
    }


def _bounded_limit(arguments: Dict[str, Any], name: str, default: int, upper: int) -> int:
    try:
        return max(1, min(upper, int(arguments.get(name, default))))
    except (TypeError, ValueError):
        return default


def _handle_dhee_inbox(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    from dhee.core.live_context import live_context_inbox

    repo = arguments.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    return live_context_inbox(
        get_db(),
        user_id=_default_user_id(arguments),
        repo=repo,
        cwd=repo,
        workspace_id=arguments.get("workspace_id") or repo,
        project_id=arguments.get("project_id"),
        channel=arguments.get("channel"),
        consumer_id=arguments.get("consumer_id"),
        agent_id=str(arguments.get("agent_id") or _default_agent_id(arguments)),
        harness=str(arguments.get("harness") or os.environ.get("DHEE_HARNESS") or _default_agent_id(arguments)),
        runtime_id=str(arguments.get("harness") or os.environ.get("DHEE_HARNESS") or _default_agent_id(arguments)),
        session_id=arguments.get("session_id"),
        native_session_id=arguments.get("session_id"),
        limit=_bounded_limit(arguments, "limit", 10, 50),
        mark_read=bool(arguments.get("mark_read", True)),
        include_own=bool(arguments.get("include_own", False)),
    )


def _handle_dhee_broadcast(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    _maybe_sync_codex_runtime(arguments)
    from dhee.core.live_context import broadcast_live_context

    metadata = arguments.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return {"error": "metadata must be an object"}
    repo = arguments.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    return broadcast_live_context(
        get_db(),
        user_id=_default_user_id(arguments),
        body=str(arguments.get("body") or ""),
        title=arguments.get("title"),
        repo=repo,
        cwd=repo,
        workspace_id=arguments.get("workspace_id") or repo,
        project_id=arguments.get("project_id"),
        target_project_id=arguments.get("target_project_id"),
        channel=arguments.get("channel"),
        message_kind=str(arguments.get("message_kind") or "broadcast"),
        session_id=arguments.get("session_id"),
        task_id=arguments.get("task_id"),
        metadata=metadata or {},
        agent_id=str(arguments.get("agent_id") or _default_agent_id(arguments)),
        harness=str(arguments.get("harness") or os.environ.get("DHEE_HARNESS") or _default_agent_id(arguments)),
    )


def _handle_dhee_read(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee import runtime

    runtime_result = runtime.execute_router("read", arguments)
    if runtime_result is not None:
        return runtime_result

    from dhee.router.handlers import handle_dhee_read
    return handle_dhee_read(arguments)


def _handle_dhee_bash(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee import runtime

    runtime_result = runtime.execute_router("bash", arguments)
    if runtime_result is not None:
        return runtime_result

    from dhee.router.handlers import handle_dhee_bash
    return handle_dhee_bash(arguments)


def _handle_dhee_agent(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.router.handlers import handle_dhee_agent
    return handle_dhee_agent(arguments)


def _handle_dhee_grep(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee import runtime

    runtime_result = runtime.execute_router("grep", arguments)
    if runtime_result is not None:
        return runtime_result

    from dhee.router.handlers import handle_dhee_grep
    return handle_dhee_grep(arguments)


def _handle_dhee_expand_result(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.router.handlers import handle_dhee_expand_result
    return handle_dhee_expand_result(arguments)


HANDLERS = {
    "remember": _handle_remember,
    "search_memory": _handle_search_memory,
    "get_memory": _handle_get_memory,
    "get_all_memories": _handle_get_all_memories,
    "dhee_context": _handle_dhee_context,
    "dhee_scene_world_route": _handle_dhee_scene_world_route,
    "dhee_scene_compile": _handle_dhee_scene_compile,
    "dhee_scene_search": _handle_dhee_scene_search,
    "dhee_context_pack": _handle_dhee_context_pack,
    "dhee_repo_brain_index": _handle_dhee_repo_brain_index,
    "dhee_repo_brain_get": _handle_dhee_repo_brain_get,
    "dhee_repo_brain_localize": _handle_dhee_repo_brain_localize,
    "dhee_repo_graph_export": _handle_dhee_repo_graph_export,
    "dhee_context_graph_query": _handle_dhee_context_graph_query,
    "dhee_repo_symbol_search": _handle_dhee_repo_symbol_search,
    "dhee_repo_callers": _handle_dhee_repo_callers,
    "dhee_repo_callees": _handle_dhee_repo_callees,
    "dhee_repo_impact": _handle_dhee_repo_impact,
    "dhee_repo_explore": _handle_dhee_repo_explore,
    "dhee_temporal_fact_assert": _handle_dhee_temporal_fact_assert,
    "dhee_temporal_fact_search": _handle_dhee_temporal_fact_search,
    "dhee_temporal_fact_get": _handle_dhee_temporal_fact_get,
    "dhee_temporal_fact_invalidate": _handle_dhee_temporal_fact_invalidate,
    "dhee_temporal_fact_stats": _handle_dhee_temporal_fact_stats,
    "dhee_task_contract_compile": _handle_dhee_task_contract_compile,
    "dhee_task_contract_create": _handle_dhee_task_contract_create,
    "dhee_task_contract_list": _handle_dhee_task_contract_list,
    "dhee_task_contract_get": _handle_dhee_task_contract_get,
    "dhee_task_contract_import": _handle_dhee_task_contract_import,
    "dhee_task_contract_interpret": _handle_dhee_task_contract_interpret,
    "dhee_contract_supervise_action": _handle_dhee_contract_supervise_action,
    "dhee_contract_record_observation": _handle_dhee_contract_record_observation,
    "dhee_contract_run_verification": _handle_dhee_contract_run_verification,
    "dhee_contract_proof_bundle": _handle_dhee_contract_proof_bundle,
    "dhee_contract_runtime_activate": _handle_dhee_contract_runtime_activate,
    "dhee_contract_runtime_status": _handle_dhee_contract_runtime_status,
    "dhee_contract_runtime_deactivate": _handle_dhee_contract_runtime_deactivate,
    "dhee_contract_enforcement_set": _handle_dhee_contract_enforcement_set,
    "dhee_contract_enforcement_status": _handle_dhee_contract_enforcement_status,
    "dhee_contract_runtime_doctor": _handle_dhee_contract_runtime_doctor,
    "dhee_update_capsule_create": _handle_dhee_update_capsule_create,
    "dhee_update_capsule_list": _handle_dhee_update_capsule_list,
    "dhee_update_capsule_get": _handle_dhee_update_capsule_get,
    "dhee_update_capsule_import": _handle_dhee_update_capsule_import,
    "dhee_update_capsule_interpret": _handle_dhee_update_capsule_interpret,
    "get_last_session": _handle_get_last_session,
    "save_session_digest": _handle_save_session_digest,
    "get_memory_stats": _handle_get_memory_stats,
    "audit_memory_quality": _handle_audit_memory_quality,
    "repair_memory_quality": _handle_repair_memory_quality,
    "search_skills": _handle_search_skills,
    "apply_skill": _handle_apply_skill,
    "log_skill_outcome": _handle_log_skill_outcome,
    "record_trajectory_step": _handle_record_trajectory_step,
    "mine_skills": _handle_mine_skills,
    "get_skill_stats": _handle_get_skill_stats,
    "search_skills_structural": _handle_search_skills_structural,
    "analyze_skill_gaps": _handle_analyze_skill_gaps,
    "decompose_skill": _handle_decompose_skill,
    "apply_skill_with_bindings": _handle_apply_skill_with_bindings,
    "enrich_pending": _handle_enrich_pending,
    "think": _handle_think,
    "anticipate": _handle_anticipate,
    "record_outcome": _handle_record_outcome,
    "reflect": _handle_reflect,
    "store_intention": _handle_store_intention,
    "dhee_submit_learning": _handle_dhee_submit_learning,
    "dhee_search_learnings": _handle_dhee_search_learnings,
    "dhee_promote_learning": _handle_dhee_promote_learning,
    "dhee_context_status": _handle_dhee_context_status,
    "dhee_context_state": _handle_dhee_context_state,
    "dhee_context_checkpoint": _handle_dhee_context_checkpoint,
    "dhee_context_rollover": _handle_dhee_context_rollover,
    "dhee_context_provision": _handle_dhee_context_provision,
    "dhee_tools_list": _handle_dhee_tools_list,
    "dhee_shell": _handle_dhee_shell,
    "dhee_list_assets": _handle_dhee_list_assets,
    "dhee_get_asset": _handle_dhee_get_asset,
    "dhee_sync_codex_artifacts": _handle_dhee_sync_codex_artifacts,
    "dhee_why": _handle_dhee_why,
    "dhee_thread_state": _handle_dhee_thread_state,
    "dhee_shared_task": _handle_dhee_shared_task,
    "dhee_shared_task_results": _handle_dhee_shared_task_results,
    "dhee_inbox": _handle_dhee_inbox,
    "dhee_broadcast": _handle_dhee_broadcast,
    "dhee_context_bootstrap": _handle_dhee_context_bootstrap,
    "dhee_handoff": _handle_dhee_handoff,
    "dhee_read": _handle_dhee_read,
    "dhee_bash": _handle_dhee_bash,
    "dhee_agent": _handle_dhee_agent,
    "dhee_grep": _handle_dhee_grep,
    "dhee_expand_result": _handle_dhee_expand_result,
}

_MEMORY_FREE_TOOLS = {
    "get_last_session", "save_session_digest",
    "record_outcome", "reflect", "store_intention",
    "dhee_scene_world_route", "dhee_scene_compile", "dhee_scene_search", "dhee_context_pack",
    *CONTEXT_COMPILER_TOOL_NAMES,
    "dhee_submit_learning", "dhee_search_learnings", "dhee_promote_learning",
    "dhee_context_status", "dhee_context_state", "dhee_context_checkpoint", "dhee_context_rollover", "dhee_context_provision", "dhee_tools_list", "dhee_shell",
    "dhee_list_assets", "dhee_get_asset", "dhee_sync_codex_artifacts", "dhee_why", "dhee_thread_state", "dhee_shared_task", "dhee_shared_task_results", "dhee_inbox", "dhee_broadcast", "dhee_handoff",
    "dhee_read", "dhee_bash", "dhee_agent", "dhee_grep", "dhee_expand_result",
}



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
