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
 9. search_skills        — Semantic search over skills
10. apply_skill          — Inject skill recipe into context
11. log_skill_outcome    — Report success/failure for a skill
12. record_trajectory_step — Record a step in active trajectory
13. mine_skills          — Run skill mining cycle
14. get_skill_stats      — Statistics about skills and trajectories
15. search_skills_structural — Find skills by structural similarity
16. analyze_skill_gaps   — Show what transfers vs what needs experimentation
17. decompose_skill      — Trigger structural decomposition of a flat skill
18. apply_skill_with_bindings — Apply skill with slot values, includes gap analysis
19. enrich_pending       — Batch-enrich deferred memories
20. think                — Cognitive decomposition (memory-grounded reasoning)
21. anticipate           — Proactive scene + intention surfacing (Buddhi-powered)
22. record_outcome       — Report task outcome for performance tracking
23. reflect              — Agent-triggered insight synthesis
24. store_intention      — Store a future trigger (prospective memory)
25. dhee_list_assets     — List stored host-parsed artifacts
26. dhee_get_asset       — Inspect a stored artifact and its bindings/chunks
27. dhee_sync_codex_artifacts — Ingest Codex session logs into the artifact store
28. dhee_why              — Explain memory/artifact provenance and lineage
29. dhee_handoff          — Emit a structured resume snapshot for a new harness
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from dhee.memory.main import FullMemory
from dhee.configs.base import (
    MemoryConfig,
    VectorStoreConfig,
    LLMConfig,
    EmbedderConfig,
    FadeMemConfig,
)

logger = logging.getLogger(__name__)


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
    harness = str(
        arguments.get("harness")
        or os.environ.get("DHEE_HARNESS")
        or os.environ.get("DHEE_AGENT_ID")
        or ""
    ).strip().lower()
    if harness != "codex":
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
    EMBEDDING_DIMS = {
        "models/text-embedding-005": 768,
        "text-embedding-005": 768,
        "gemini-embedding-001": 3072,
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }
    env_dims = os.environ.get("DHEE_EMBEDDING_DIMS") or os.environ.get("FADEM_EMBEDDING_DIMS")
    if env_dims:
        return int(env_dims)
    if model in EMBEDDING_DIMS:
        return EMBEDDING_DIMS[model]
    if provider == "gemini":
        return 3072
    elif provider == "openai":
        return 1536
    return 3072


def get_memory_instance() -> FullMemory:
    """Create and return a configured FullMemory instance for the MCP server."""
    from dhee.cli_config import get_api_key

    openai_key = get_api_key("openai")
    gemini_key = get_api_key("gemini")
    nvidia_key = get_api_key("nvidia")

    def _env(key: str, default: str = "") -> str:
        """Read DHEE_ env var with FADEM_ fallback for backward compat."""
        return os.environ.get(f"DHEE_{key}") or os.environ.get(f"FADEM_{key}") or default

    if openai_key:
        embedder_model = _env("EMBEDDER_MODEL", "text-embedding-3-small")
        embedding_dims = _get_embedding_dims_for_model(embedder_model, "openai")
        llm_config = LLMConfig(
            provider="openai",
            config={
                "model": _env("LLM_MODEL", "gpt-4o-mini"),
                "temperature": 0.1, "max_tokens": 1024, "api_key": openai_key,
            }
        )
        embedder_config = EmbedderConfig(
            provider="openai",
            config={"model": embedder_model, "api_key": openai_key},
        )
    elif gemini_key:
        embedder_model = _env("EMBEDDER_MODEL", "gemini-embedding-001")
        embedding_dims = _get_embedding_dims_for_model(embedder_model, "gemini")
        llm_config = LLMConfig(
            provider="gemini",
            config={
                "model": _env("LLM_MODEL", "gemini-2.0-flash"),
                "temperature": 0.1, "max_tokens": 1024, "api_key": gemini_key,
            }
        )
        embedder_config = EmbedderConfig(
            provider="gemini",
            config={"model": embedder_model, "api_key": gemini_key},
        )
    elif nvidia_key:
        embedder_model = _env("EMBEDDER_MODEL", "nvidia/llama-nemotron-embed-vl-1b-v2")
        embedding_dims = 2048
        llm_config = LLMConfig(
            provider="nvidia",
            config={
                "model": _env("LLM_MODEL", "qwen/qwen3.5-397b-a17b"),
                "temperature": 0.2, "max_tokens": 4096, "api_key": nvidia_key,
            }
        )
        embedder_config = EmbedderConfig(
            provider="nvidia",
            config={"model": embedder_model, "api_key": nvidia_key},
        )
    else:
        # Zero-config: SimpleEmbedder + MockLLM
        embedding_dims = 384
        llm_config = LLMConfig(provider="mock", config={})
        embedder_config = EmbedderConfig(
            provider="simple", config={"embedding_dims": 384},
        )

    from dhee.configs.base import _dhee_data_dir
    vec_db_path = _env("VEC_DB_PATH") or os.path.join(_dhee_data_dir(), "zvec")
    collection = _env("COLLECTION", "dhee_memories")

    # Use in-memory vector store for simple embedder (no persistent storage needed)
    if embedder_config.provider == "simple":
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

    history_db_path = _env("HISTORY_DB") or os.path.join(_dhee_data_dir(), "history.db")

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

server = Server("dhee")

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
        description="Search memory for relevant memories by semantic query. The UserPromptSubmit hook handles background search automatically — call this tool only for explicit user recall requests such as 'what did we discuss about X?' or 'recall my preference for Y'.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query - what you're trying to remember"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
                "agent_id": {"type": "string", "description": "Agent identifier to scope search to (optional)"},
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
        description="HyperAgent session bootstrap. Call at conversation start to get EVERYTHING: performance trends, synthesized insights from prior runs, relevant skills, pending intentions, proactive warnings, and top memories. This single call turns any agent into a HyperAgent with persistent memory and self-improvement awareness.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User identifier to load context for (default: 'default')"},
                "task_description": {"type": "string", "description": "What the agent is about to work on — used to filter relevant insights, skills, and performance history"},
                "limit": {"type": "integer", "description": "Maximum number of memories to return (default: 10)"},
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
        name="dhee_handoff",
        description=(
            "Emit a structured handoff snapshot for cross-harness or cross-machine "
            "resume. Prefers live thread state when `thread_id` is provided; "
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
            "`ptr`, and returns only the digest. Use this INSTEAD OF native "
            "`Read` to keep large file contents out of the conversation "
            "context. If the digest is insufficient, call "
            "`dhee_expand_result(ptr=...)` to retrieve the raw."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file"},
                "offset": {"type": "integer", "description": "1-indexed start line (optional)"},
                "limit": {"type": "integer", "description": "Number of lines to read from offset (optional)"},
                "digest_depth": {
                    "type": "string",
                    "enum": ["shallow", "normal", "deep"],
                    "description": "shallow=counts+symbols only; normal=+5-line head/tail; deep=+10-line head/tail. Default: normal",
                },
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
            },
            "required": ["ptr"],
        },
    ),
]


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
    return hyper_ctx.to_dict()


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
                "metadata": row.get("metadata") or {},
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


def _handle_dhee_read(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.router.handlers import handle_dhee_read
    return handle_dhee_read(arguments)


def _handle_dhee_bash(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.router.handlers import handle_dhee_bash
    return handle_dhee_bash(arguments)


def _handle_dhee_agent(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.router.handlers import handle_dhee_agent
    return handle_dhee_agent(arguments)


def _handle_dhee_grep(_memory, arguments: Dict[str, Any]) -> Dict[str, Any]:
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
    "get_last_session": _handle_get_last_session,
    "save_session_digest": _handle_save_session_digest,
    "get_memory_stats": _handle_get_memory_stats,
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
    "dhee_list_assets": _handle_dhee_list_assets,
    "dhee_get_asset": _handle_dhee_get_asset,
    "dhee_sync_codex_artifacts": _handle_dhee_sync_codex_artifacts,
    "dhee_why": _handle_dhee_why,
    "dhee_thread_state": _handle_dhee_thread_state,
    "dhee_shared_task": _handle_dhee_shared_task,
    "dhee_shared_task_results": _handle_dhee_shared_task_results,
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
    "dhee_list_assets", "dhee_get_asset", "dhee_sync_codex_artifacts", "dhee_why", "dhee_thread_state", "dhee_shared_task", "dhee_shared_task_results", "dhee_handoff",
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
