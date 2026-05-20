"""Dhee compact MCP server. Compiled state + pointer-backed tools.

Install: pip install dhee[openai,mcp]
Config:  export OPENAI_API_KEY=sk-...
Run:     dhee-mcp

Core tools:
 - dhee_context_* — compiled state, debt, checkpoint, rollover, provision
 - dhee_read / dhee_grep / dhee_bash / dhee_agent — pointer-backed router
 - dhee_shell — DheeFS virtual learning/context space
 - dhee_handoff / dhee_inbox / dhee_broadcast — continuity and live context
 - remember / recall / learning search — compact memory and promoted playbooks

Cost model:
  Hot path (remember/recall): ~$0.0002 per call (1 embedding only)
  Checkpoint: ~$0.001 per 10 memories enriched (1 LLM batch call)
  Enrichment adds echo paraphrases + keywords → dramatically better recall quality.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent, ToolAnnotations
except ModuleNotFoundError:
    class Tool:  # type: ignore[no-redef]
        def __init__(self, *, name: str, description: str, inputSchema: Dict[str, Any], annotations: Any = None):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema
            self.annotations = annotations

    class ToolAnnotations:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any):
            self.__dict__.update(kwargs)

    class TextContent:  # type: ignore[no-redef]
        def __init__(self, *, type: str, text: str):
            self.type = type
            self.text = text

    class Server:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any):
            self._list_tools = None
            self._call_tool = None

        def list_tools(self):
            def decorator(func):
                self._list_tools = func
                return func
            return decorator

        def call_tool(self):
            def decorator(func):
                self._call_tool = func
                return func
            return decorator

        def create_initialization_options(self):
            return {}

        async def run(self, *_args: Any, **_kwargs: Any):
            raise RuntimeError("mcp package is required to run dhee-mcp")

    class _MissingStdioServer:
        async def __aenter__(self):
            raise RuntimeError("mcp package is required to run dhee-mcp")

        async def __aexit__(self, *_args: Any):
            return False

    def stdio_server():  # type: ignore[no-redef]
        return _MissingStdioServer()

logger = logging.getLogger(__name__)

from dhee.mcp_registry import CONTEXT_COMPILER_TOOL_NAMES, make_tools

_MCP_CONTEXT_FIRST_INSTRUCTIONS = (
    "Dhee is the native memory and context-router. At the start of substantive "
    "repo/workspace tasks, use Dhee context/recall before reconstructing from "
    "local files or shell output. Prefer one dhee_context_bootstrap call with "
    "the repo path; it bundles handoff, active shared task, shared results, and "
    "unread inbox. Use dhee_broadcast for context another active agent must see "
    "immediately. Search promoted Dhee/Hermes learnings when prior evolution "
    "may apply. Prefer dhee_read and dhee_bash for large reusable "
    "reads/searches/commands so raw output stays behind pointers. When "
    "DHEE_HARNESS=codex, Dhee syncs Codex session logs on context/collaboration "
    "calls so Codex native tool progress becomes shared Dhee context."
)

# ---------------------------------------------------------------------------
# Lazy singleton — DheePlugin wraps Engram + Buddhi
# ---------------------------------------------------------------------------

_plugin = None


def _get_plugin():
    """Create the DheePlugin singleton. Wraps Engram + Buddhi."""
    global _plugin
    if _plugin is None:
        from dhee.plugin import DheePlugin
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


def _get_db():
    return _get_plugin().memory.db


def _default_user_id(args: Dict[str, Any]) -> str:
    return str(args.get("user_id") or os.environ.get("DHEE_USER_ID") or "default")


def _default_agent_id(args: Dict[str, Any]) -> str:
    return str(args.get("agent_id") or os.environ.get("DHEE_AGENT_ID") or "agent")


# ---------------------------------------------------------------------------
# Compact default tool surface
# ---------------------------------------------------------------------------

server = Server("dhee", instructions=_MCP_CONTEXT_FIRST_INSTRUCTIONS)

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
            "Use before local reconstruction when prior repo/user context may exist. "
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
            "Use before local reconstruction on substantive repo/workspace tasks. "
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
                "repo": {
                    "type": "string",
                    "description": "Optional repo/workspace root to scope promoted learnings",
                },
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
        description="Compile a private TemporalScene card from evidence pointers or admitted derivatives.",
        inputSchema={
            "type": "object",
            "properties": {
                "evidence": {"type": "array", "items": {"type": "object"}},
                "query": {"type": "string"},
                "task": {"type": "string"},
                "title": {"type": "string"},
                "repo": {"type": "string"},
                "user_id": {"type": "string"},
                "privacy_scope": {"type": "string"},
                "store_dir": {"type": "string"},
                "save": {"type": "boolean"},
                "include_recent_memories": {"type": "boolean"},
                "include_repo_context": {"type": "boolean"},
                "include_session": {"type": "boolean"},
                "include_shared_task_results": {"type": "boolean"},
                "include_artifacts": {"type": "boolean"},
                "include_live_sources": {"type": "boolean"},
                "sources": {"type": "array", "items": {"type": "string"}},
                "session": {"type": "object"},
                "shared_task_results": {},
                "artifacts": {},
                "limit": {"type": "integer"},
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
        description="Build a hard-budget context pack from scene cards. Raw evidence expands only by pointer.",
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
        name="dhee_submit_learning",
        description="Submit an auditable learning candidate. Candidates are not injected until promoted.",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "kind": {"type": "string", "enum": ["skill", "heuristic", "policy", "contrast", "memory", "workflow", "playbook"]},
                "source_agent_id": {"type": "string"},
                "source_harness": {"type": "string"},
                "task_type": {"type": "string"},
                "repo": {"type": "string"},
                "scope": {"type": "string", "enum": ["personal", "repo", "workspace"]},
                "confidence": {"type": "number"},
                "utility": {"type": "number"},
                "evidence": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["title", "body"],
        },
    ),
    Tool(
        name="dhee_search_learnings",
        description="Search promoted Dhee learnings. Include candidates only for explicit review workflows.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "task_type": {"type": "string"},
                "repo": {"type": "string"},
                "status": {"type": "string", "enum": ["candidate", "promoted", "rejected", "archived"]},
                "include_candidates": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
        },
    ),
    Tool(
        name="dhee_promote_learning",
        description="Promote a learning after gate/approval. Repo and workspace promotions require explicit approval.",
        inputSchema={
            "type": "object",
            "properties": {
                "learning_id": {"type": "string"},
                "scope": {"type": "string", "enum": ["personal", "repo", "workspace"]},
                "repo": {"type": "string"},
                "approved_by": {"type": "string"},
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
        description="List Dhee's compact default MCP tools and the advanced tools available in dhee-mcp-full.",
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
                "command": {"type": "string", "description": "DheeFS command, e.g. `ls /learnings`"},
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id override"},
                "user_id": {"type": "string", "description": "User identifier (default: default)"},
                "agent_id": {"type": "string", "description": "Agent identity for mutating commands"},
            },
            "required": ["command"],
        },
    ),
    Tool(
        name="dhee_inbox",
        description=(
            "Fetch unread live shared-context broadcasts for this active agent. "
            "Call after context/recall and after substantial shared work; a "
            "non-empty signal means another party broadcast context to read before continuing."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id or path override"},
                "project_id": {"type": "string", "description": "Optional project scope"},
                "channel": {"type": "string", "description": "Optional channel filter"},
                "consumer_id": {"type": "string", "description": "Stable consumer id"},
                "agent_id": {"type": "string", "description": "Agent identity"},
                "harness": {"type": "string", "description": "Harness/runtime id"},
                "session_id": {"type": "string", "description": "Native session id"},
                "limit": {"type": "integer", "description": "Max unread messages (default 10)"},
                "mark_read": {"type": "boolean", "description": "Mark returned messages read (default true)"},
                "include_own": {"type": "boolean", "description": "Include own messages"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
            },
        },
    ),
    Tool(
        name="dhee_broadcast",
        description=(
            "Publish live shared context to the workspace line so other active "
            "agents and UI subscribers receive it immediately."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "Broadcast body/message"},
                "title": {"type": "string", "description": "Short title"},
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "workspace_id": {"type": "string", "description": "Explicit workspace id or path override"},
                "project_id": {"type": "string", "description": "Source project id"},
                "target_project_id": {"type": "string", "description": "Target project id"},
                "channel": {"type": "string", "description": "Optional channel"},
                "message_kind": {"type": "string", "description": "Kind label, default broadcast"},
                "session_id": {"type": "string", "description": "Native session id"},
                "task_id": {"type": "string", "description": "Related task id"},
                "metadata": {"type": "object", "description": "Optional metadata"},
                "agent_id": {"type": "string", "description": "Agent identity"},
                "harness": {"type": "string", "description": "Harness/runtime id"},
                "user_id": {"type": "string", "description": "User identifier (default: 'default')"},
            },
            "required": ["body"],
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
    Tool(
        name="dhee_read",
        description=(
            "Router wrapper for Read. Opens a file, extracts a factual digest "
            "(path + line/char/token counts, symbols for Python/Markdown/JSON/"
            "JS/TS/Go/Rust, head+tail excerpt), stores the full raw content "
            "under a pointer `ptr`, and returns only the digest. Use INSTEAD "
            "OF native `Read` to keep large file contents out of the "
            "conversation context. If the digest is insufficient, call "
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
                "query": {"type": "string", "description": "Optional task/query for task-aware digest schema"},
                "task_intent": {"type": "string", "description": "Optional digest intent: find_definition, debug_failure, understand_module, inspect_config, general"},
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="dhee_grep",
        description="Pointer-backed ripgrep. Returns compact match counts and top hits; raw hit list stays behind ptr.",
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex or literal when fixed_string=true"},
                "path": {"type": "string", "description": "File or directory root"},
                "glob": {"type": "string", "description": "Optional glob filter"},
                "case_insensitive": {"type": "boolean", "description": "Case-insensitive search"},
                "fixed_string": {"type": "boolean", "description": "Treat pattern as literal"},
                "multiline": {"type": "boolean", "description": "Enable multiline matching"},
                "context": {"type": "integer", "description": "Context lines"},
            },
            "required": ["pattern"],
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
            "pasted docs, etc.). Extracts file:line refs, headings, bullets, "
            "typed digest schemas, error signals, and head+tail from the text; stores the full raw "
            "under `ptr`. Use INSTEAD OF pasting a subagent's full response "
            "back into your reasoning."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Raw text to digest"},
                "kind": {
                    "type": "string",
                    "description": "Optional hint: LocalizationDigest, BugReproDigest, ReadDigest, SearchDigest, or legacy labels.",
                },
                "source": {"type": "string", "description": "Optional label (e.g. 'subagent:Explore')"},
            },
            "required": ["text"],
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
                "harness": {"type": "string", "description": "Harness/runtime id, e.g. codex"},
                "session_id": {"type": "string", "description": "Native active session id"},
                "user_id": {"type": "string", "description": "User identifier"},
                "memory_limit": {"type": "integer", "description": "Recent memories to include"},
                "artifact_limit": {"type": "integer", "description": "Recent artifacts to include"},
                "task_limit": {"type": "integer", "description": "Recent tasks to include"},
                "intention_limit": {"type": "integer", "description": "Active intentions to include"},
                "result_limit": {"type": "integer", "description": "Shared task results to include"},
                "inbox_limit": {"type": "integer", "description": "Unread broadcasts to include"},
                "include_own": {"type": "boolean", "description": "Include own live broadcasts"},
            },
        },
    ),
    Tool(
        name="dhee_handoff",
        description="Emit compact cross-agent continuity for a repo before local reconstruction.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo/workspace path"},
                "thread_id": {"type": "string", "description": "Optional live thread id"},
                "user_id": {"type": "string", "description": "User identifier"},
                "memory_limit": {"type": "integer", "description": "Recent memories to include"},
                "artifact_limit": {"type": "integer", "description": "Recent artifacts to include"},
                "task_limit": {"type": "integer", "description": "Recent tasks to include"},
                "intention_limit": {"type": "integer", "description": "Active intentions to include"},
            },
        },
    ),
]


_READ_ONLY_TOOL_HINTS = {
    "recall",
    "context",
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
    "dhee_inbox",
    "dhee_read",
    "dhee_grep",
    "dhee_agent",
    "dhee_expand_result",
    "dhee_context_bootstrap",
    "dhee_handoff",
}

for _tool in TOOLS:
    if _tool.name in _READ_ONLY_TOOL_HINTS:
        _tool.annotations = ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )


# ---------------------------------------------------------------------------
# Tool-schema footprint reporting (cache-safe; does not mutate tool registry)
# ---------------------------------------------------------------------------

def _schema_tokens(payload: Any) -> int:
    return max(0, int(len(json.dumps(payload, sort_keys=True, default=str)) / 3.5))


def _trim_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _tool_schema_dict(tool: Tool) -> Dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.inputSchema,
    }


def _slim_schema_payload(tool: Tool, tier: str) -> Dict[str, Any]:
    schema = tool.inputSchema or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if tier == "max":
        return {"name": tool.name}
    if tier == "strong":
        return {
            "name": tool.name,
            "description": _trim_text(tool.description, 80),
            "properties": sorted(props.keys()) if isinstance(props, dict) else [],
            "required": required,
        }
    if tier == "moderate":
        slim_props = {}
        if isinstance(props, dict):
            for key, value in props.items():
                value = value if isinstance(value, dict) else {}
                slim_props[key] = {
                    "type": value.get("type"),
                    "description": _trim_text(value.get("description"), 80),
                }
        return {
            "name": tool.name,
            "description": _trim_text(tool.description, 160),
            "inputSchema": {
                "type": schema.get("type", "object") if isinstance(schema, dict) else "object",
                "properties": slim_props,
                "required": required,
            },
        }
    return _tool_schema_dict(tool)


def tool_schema_report() -> Dict[str, Any]:
    original_payloads = [_tool_schema_dict(tool) for tool in TOOLS]
    original_tokens = _schema_tokens(original_payloads)
    tiers: Dict[str, Dict[str, Any]] = {}
    for tier in ("low", "moderate", "strong", "max"):
        payload = [_slim_schema_payload(tool, tier) for tool in TOOLS]
        tokens = _schema_tokens(payload)
        tiers[tier] = {
            "tokens": tokens,
            "saved_tokens": max(0, original_tokens - tokens),
            "saved_pct": round((original_tokens - tokens) / original_tokens * 100, 2) if original_tokens else 0.0,
        }
    return {
        "tool_count": len(TOOLS),
        "original_tokens": original_tokens,
        "tiers": tiers,
        "policy": "Report slim tiers only; do not mutate tool definitions mid-session. Mask availability instead.",
    }


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


_DEFAULT_RECALL_THRESHOLD = 0.6


def _recall_threshold(args: Dict[str, Any]) -> float:
    """Resolve the per-call recall threshold.

    Precedence: explicit ``threshold`` arg → env override → default 0.6.
    Negative or zero disables filtering (caller wants raw results).
    """
    if "threshold" in args and args["threshold"] is not None:
        try:
            return float(args["threshold"])
        except (TypeError, ValueError):
            pass
    env = os.environ.get("DHEE_RECALL_THRESHOLD")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return _DEFAULT_RECALL_THRESHOLD


_RECALL_TOKEN_RE = None


def _tokenise(text: str) -> set:
    """Crude lowercase word-set used to compute the per-result ``why``.

    Intentionally cheap: no stemming, no stopword list. Match overlap
    here is a transparency signal, not a relevance score — the
    embedding score is the source of truth for ranking.
    """
    global _RECALL_TOKEN_RE
    if _RECALL_TOKEN_RE is None:
        import re as _re

        _RECALL_TOKEN_RE = _re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
    out = {m.lower() for m in _RECALL_TOKEN_RE.findall(text or "")}
    out.discard("the")
    out.discard("and")
    out.discard("for")
    out.discard("from")
    out.discard("with")
    out.discard("that")
    out.discard("this")
    out.discard("how")
    out.discard("what")
    return out


def _recall_why(query: str, memory_text: str, *, max_terms: int = 5) -> str:
    """Return a short comma-list of overlapping query/memory terms.

    Helps the model decide whether a low-mid score result is genuine.
    Empty string when there's no overlap (we still return the result if
    score passed threshold — embedding match without lexical overlap is
    legitimate, just unexplained).
    """
    qt = _tokenise(query)
    mt = _tokenise(memory_text)
    if not qt or not mt:
        return ""
    shared = qt & mt
    if not shared:
        return ""
    ordered = sorted(shared, key=lambda t: -len(t))[:max_terms]
    return ", ".join(ordered)


def _handle_recall(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search memory. 0 LLM calls, 1 embed.

    Fuses personal memory hits with shared entries from any linked
    repo containing the request's ``cwd`` (or the process cwd when not
    supplied), so a coding agent sitting in a linked repo sees both
    its user's personal memory and the team's shared context.

    Quality controls:

    * **Threshold filter** — drops results whose composite score is
      below ``DHEE_RECALL_THRESHOLD`` (default 0.6). Honest empty is
      better than misleading low-score noise: the model doesn't waste
      tokens or get biased by tangentially-related memories. Override
      per-call with ``threshold`` in args, or globally via env.
    * **``why`` field** — lists overlapping query/memory terms so the
      caller can sanity-check whether the match is real.
    """
    query = args.get("query", "")
    if not query:
        return {"error": "query is required"}

    plugin = _get_plugin()
    user_id = args.get("user_id", "default")
    limit = min(max(1, int(args.get("limit", 5))), 20)
    cwd = args.get("cwd") or os.getcwd()
    threshold = _recall_threshold(args)

    # Pull a bigger raw window so the threshold filter doesn't starve
    # the caller's ``limit``. Cap is conservative to keep one embed call
    # cheap.
    raw_limit = min(max(limit * 3, limit), 30)

    raw_result = plugin._engram._memory.search(
        query=query, user_id=user_id, limit=raw_limit,
    )
    results = raw_result.get("results", []) if isinstance(raw_result, dict) else []

    try:
        from dhee import repo_link
        fused = repo_link.fuse_search_results(query, results, cwd=cwd, limit=raw_limit)
    except Exception:
        fused = list(results)

    memories: List[Dict[str, Any]] = []
    dropped_count = 0
    lowest_kept_score = None
    for r in fused:
        score = float(r.get("composite_score", r.get("score", 0)) or 0)
        if threshold > 0 and score < threshold:
            dropped_count += 1
            continue
        text = r.get("memory", "") or ""
        memories.append({
            "id": r.get("id"),
            "memory": text,
            "score": round(score, 3),
            "source": r.get("source", "personal"),
            "repo_root": r.get("repo_root"),
            "title": r.get("title"),
            "why": _recall_why(query, text),
        })
        lowest_kept_score = score if lowest_kept_score is None else min(lowest_kept_score, score)
        if len(memories) >= limit:
            break

    response: Dict[str, Any] = {
        "memories": memories,
        "count": len(memories),
        "threshold": round(threshold, 3),
        "dropped_below_threshold": dropped_count,
    }
    if not memories and dropped_count:
        # Be visibly honest about why nothing came back. The caller can
        # lower the threshold per-call or via env if they want raw
        # results.
        response["note"] = (
            f"All {dropped_count} candidates fell below threshold "
            f"{threshold:.2f}. Raise --threshold or set "
            f"DHEE_RECALL_THRESHOLD=0 to inspect them."
        )

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
        repo=args.get("repo"),
    )


def _handle_dhee_submit_learning(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.learnings import LearningExchange

    candidate = LearningExchange().submit(
        title=str(args.get("title") or ""),
        body=str(args.get("body") or ""),
        kind=str(args.get("kind") or "heuristic"),
        source_agent_id=str(args.get("source_agent_id") or _default_agent_id(args)),
        source_harness=str(args.get("source_harness") or os.environ.get("DHEE_HARNESS") or "mcp"),
        task_type=args.get("task_type"),
        repo=args.get("repo"),
        scope=str(args.get("scope") or "personal"),
        confidence=float(args.get("confidence", 0.5) or 0.5),
        utility=float(args.get("utility", 0.0) or 0.0),
        evidence=args.get("evidence") or [],
    )
    return {"learning": candidate.to_dict()}


def _handle_dhee_search_learnings(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.learnings import LearningExchange

    rows = LearningExchange().search(
        query=args.get("query") or "",
        task_type=args.get("task_type"),
        repo=args.get("repo"),
        status=str(args.get("status") or "promoted"),
        include_candidates=bool(args.get("include_candidates", False)),
        limit=_bounded_limit(args, "limit", 10, 50),
    )
    return {"count": len(rows), "results": rows}


def _handle_dhee_promote_learning(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.learnings import LearningExchange

    candidate = LearningExchange().promote(
        str(args.get("learning_id") or ""),
        scope=str(args.get("scope") or "personal"),
        repo=args.get("repo"),
        approved_by=args.get("approved_by"),
    )
    return {"learning": candidate.to_dict()}


def _handle_dhee_shell(args: Dict[str, Any]) -> Dict[str, Any]:
    repo = args.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    from dhee import runtime

    runtime_result = runtime.execute_shell(
        str(args.get("command") or ""),
        repo=repo,
        user_id=str(args.get("user_id") or "default"),
        agent_id=_default_agent_id(args),
        workspace_id=args.get("workspace_id") or repo,
    )
    if runtime_result is not None:
        return runtime_result

    from dhee.fs import ContextWorkspace

    workspace = ContextWorkspace(
        repo=repo,
        user_id=str(args.get("user_id") or "default"),
        agent_id=_default_agent_id(args),
        db=_get_db(),
        workspace_id=args.get("workspace_id") or repo,
    )
    return workspace.execute(str(args.get("command") or "")).to_dict()


def _context_store(args: Dict[str, Any]):
    from dhee.context_state import ContextStateStore

    repo = args.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    return ContextStateStore(
        repo=repo,
        workspace_id=args.get("workspace_id") or repo,
        user_id=str(args.get("user_id") or "default"),
        agent_id=_default_agent_id(args),
    )


def _runtime_context(args: Dict[str, Any], action: str, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    from dhee import runtime

    repo = args.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    return runtime.execute_context(
        action,
        repo=repo,
        workspace_id=args.get("workspace_id") or repo,
        user_id=str(args.get("user_id") or "default"),
        agent_id=_default_agent_id(args),
        args=extra or {},
    )


def _handle_dhee_context_status(args: Dict[str, Any]) -> Dict[str, Any]:
    return _runtime_context(args, "status") or _context_store(args).status()


def _handle_dhee_context_state(args: Dict[str, Any]) -> Dict[str, Any]:
    runtime_result = _runtime_context(args, "state", {"format": str(args.get("format") or "card").lower()})
    if runtime_result is not None:
        return runtime_result
    store = _context_store(args)
    fmt = str(args.get("format") or "card").lower()
    if fmt == "json":
        return {"format": "dhee_context_state", "state": store.load(), "status": store.status()}
    if fmt == "markdown":
        return {"format": "markdown", "text": store.render_markdown()}
    return {"format": "card", "text": store.render_state_card(), "status": store.status()}


def _handle_dhee_context_checkpoint(args: Dict[str, Any]) -> Dict[str, Any]:
    reason = str(args.get("reason") or "mcp checkpoint")
    return _runtime_context(args, "checkpoint", {"reason": reason}) or _context_store(args).checkpoint(reason=reason)


def _handle_dhee_context_rollover(args: Dict[str, Any]) -> Dict[str, Any]:
    reason = str(args.get("reason") or "mcp rollover")
    return _runtime_context(args, "rollover", {"reason": reason}) or _context_store(args).rollover(reason=reason)


def _handle_dhee_context_provision(args: Dict[str, Any]) -> Dict[str, Any]:
    task = str(args.get("task") or args.get("query") or "")
    return _runtime_context(args, "provision", {"task": task}) or _context_store(args).provision(task)


def _handle_dhee_scene_world_route(args: Dict[str, Any]) -> Dict[str, Any]:
    task = str(args.get("task") or args.get("query") or "")
    if not task.strip():
        return {"error": "task is required"}
    try:
        from dhee.hooks.scene_world import route_task

        return route_task(
            task,
            repo=args.get("repo"),
            user_id=args.get("user_id", "default"),
            harness=str(args.get("harness") or os.environ.get("DHEE_HARNESS") or _default_agent_id(args)),
            top_k=_bounded_limit(args, "top_k", 4, 8),
            record=args.get("record") if "record" in args else None,
        )
    except Exception as exc:
        return {"enabled": False, "status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def _scene_evidence_from_args(args: Dict[str, Any]) -> List[Dict[str, Any]]:
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
            try:
                live_db = _get_db()
            except Exception:
                live_db = None
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
    memory = None
    if "memory" in sources:
        try:
            memory = _get_plugin().memory
        except Exception:
            memory = None
    return collect_scene_evidence(
        evidence=args.get("evidence") or [],
        memory=memory,
        query=str(args.get("query") or args.get("task") or ""),
        user_id=_default_user_id(args),
        repo=args.get("repo"),
        session=args.get("session") or live.get("session"),
        shared_task_results=args.get("shared_task_results") or live.get("shared_task_results"),
        artifacts=args.get("artifacts") or live.get("artifacts"),
        sources=sources,
        limit=_bounded_limit(args, "limit", 8, 50),
    )


def _handle_dhee_scene_compile(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.temporal_scenes import compile_scene

    evidence = _scene_evidence_from_args(args)
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


def _handle_dhee_scene_search(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_context_pack(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_repo_brain_index(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_repo_brain_get(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_repo_brain_localize(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_repo_graph_export(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_context_graph_query(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _temporal_fact_ledger(args: Dict[str, Any]):
    from dhee.temporal_fact_ledger import open_default_ledger

    return open_default_ledger(args.get("db_path"))


def _handle_dhee_temporal_fact_assert(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_temporal_fact_search(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_temporal_fact_get(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_temporal_fact_invalidate(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_temporal_fact_stats(args: Dict[str, Any]) -> Dict[str, Any]:
    ledger = _temporal_fact_ledger(args)
    try:
        return ledger.stats(user_id=str(args.get("user_id") or "default"), namespace=args.get("namespace"))
    finally:
        ledger.close()


def _handle_dhee_task_contract_compile(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_task_contract_create(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_task_contract_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.task_contracts import list_task_contracts

    return {
        "format": "dhee_task_contract_list.v1",
        "results": list_task_contracts(repo=args.get("repo")),
    }


def _handle_dhee_task_contract_get(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.task_contracts import get_task_contract

    task_id = str(args.get("task_id") or args.get("id") or "")
    if not task_id:
        return {"error": "task_id is required"}
    return get_task_contract(task_id, repo=args.get("repo"))


def _handle_dhee_task_contract_import(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.task_contracts import import_task_contract

    path = str(args.get("path") or "")
    if not path:
        return {"error": "path is required"}
    return import_task_contract(path, repo=args.get("repo"))


def _handle_dhee_task_contract_interpret(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_contract_supervise_action(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_contract_record_observation(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_contract_proof_bundle(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_contract_run_verification(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_contract_runtime_activate(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_contract_runtime_status(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.contract_runtime import contract_runtime_status

    return contract_runtime_status(repo=args.get("repo"))


def _handle_dhee_contract_runtime_deactivate(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.contract_runtime import deactivate_contract_runtime

    return deactivate_contract_runtime(
        repo=args.get("repo"),
        agent_id=args.get("agent_id"),
        reason=str(args.get("reason") or "manual"),
    )


def _handle_dhee_contract_enforcement_set(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.contract_runtime import set_contract_enforcement

    return set_contract_enforcement(
        str(args.get("mode") or ""),
        repo=args.get("repo"),
        agent_id=args.get("agent_id"),
        reason=args.get("reason"),
    )


def _handle_dhee_contract_enforcement_status(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.contract_runtime import contract_enforcement_status

    return contract_enforcement_status(repo=args.get("repo"))


def _handle_dhee_contract_runtime_doctor(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.contract_runtime import contract_runtime_doctor

    return contract_runtime_doctor(repo=args.get("repo"))


def _handle_dhee_update_capsule_create(args: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_dhee_update_capsule_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.update_capsules import list_update_capsules

    return {
        "format": "dhee_update_capsule_list.v1",
        "results": list_update_capsules(repo=args.get("repo")),
    }


def _handle_dhee_update_capsule_get(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.update_capsules import get_update_capsule

    capsule_id = str(args.get("capsule_id") or args.get("id") or "")
    if not capsule_id:
        return {"error": "capsule_id is required"}
    return get_update_capsule(capsule_id, repo=args.get("repo"))


def _handle_dhee_update_capsule_import(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.update_capsules import import_update_capsule

    path = str(args.get("path") or "")
    if not path:
        return {"error": "path is required"}
    return import_update_capsule(
        path,
        repo=args.get("repo"),
        allow_private=bool(args.get("allow_private") or False),
    )


def _handle_dhee_update_capsule_interpret(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.update_capsules import interpret_update_capsule

    capsule = args.get("capsule") or args.get("path") or args.get("capsule_id") or args.get("id")
    if not capsule:
        return {"error": "capsule, path, or capsule_id is required"}
    return interpret_update_capsule(
        capsule,
        repo=args.get("repo"),
        strict=bool(args.get("strict") or False),
    )


def _handle_dhee_tools_list(_args: Dict[str, Any]) -> Dict[str, Any]:
    default_tools = [tool.name for tool in TOOLS]
    advanced_tools = [
        "search_memory",
        "get_memory",
        "get_all_memories",
        "get_memory_stats",
        "search_skills",
        "apply_skill",
        "record_trajectory_step",
        "mine_skills",
        "reflect",
        "store_intention",
        "dhee_list_assets",
        "dhee_get_asset",
        "dhee_sync_codex_artifacts",
        "dhee_why",
        "dhee_thread_state",
        "dhee_shared_task",
        "dhee_shared_task_results",
    ]
    return {
        "format": "dhee_tools",
        "default_server": "dhee-mcp",
        "advanced_server": "dhee-mcp-full",
        "default_tools": default_tools,
        "advanced_tools": advanced_tools,
        "schema_footprint": tool_schema_report(),
        "note": "Use DheeFS paths and compiled-state tools first; switch to dhee-mcp-full only for manual administration.",
    }


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


def _bounded_limit(args: Dict[str, Any], name: str, default: int, upper: int) -> int:
    try:
        return max(1, min(upper, int(args.get(name, default))))
    except (TypeError, ValueError):
        return default


def _handle_dhee_inbox(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.live_context import live_context_inbox

    repo = args.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    harness = str(args.get("harness") or os.environ.get("DHEE_HARNESS") or _default_agent_id(args))
    return live_context_inbox(
        _get_db(),
        user_id=args.get("user_id", "default"),
        repo=repo,
        cwd=repo,
        workspace_id=args.get("workspace_id") or repo,
        project_id=args.get("project_id"),
        channel=args.get("channel"),
        consumer_id=args.get("consumer_id"),
        agent_id=_default_agent_id(args),
        harness=harness,
        runtime_id=harness,
        session_id=args.get("session_id"),
        native_session_id=args.get("session_id"),
        limit=_bounded_limit(args, "limit", 10, 50),
        mark_read=bool(args.get("mark_read", True)),
        include_own=bool(args.get("include_own", False)),
    )


def _handle_dhee_broadcast(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.live_context import broadcast_live_context

    metadata = args.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return {"error": "metadata must be an object"}
    repo = args.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    harness = str(args.get("harness") or os.environ.get("DHEE_HARNESS") or _default_agent_id(args))
    return broadcast_live_context(
        _get_db(),
        user_id=args.get("user_id", "default"),
        body=str(args.get("body") or ""),
        title=args.get("title"),
        repo=repo,
        cwd=repo,
        workspace_id=args.get("workspace_id") or repo,
        project_id=args.get("project_id"),
        target_project_id=args.get("target_project_id"),
        channel=args.get("channel"),
        message_kind=str(args.get("message_kind") or "broadcast"),
        session_id=args.get("session_id"),
        task_id=args.get("task_id"),
        metadata=metadata or {},
        agent_id=_default_agent_id(args),
        harness=harness,
    )


def _handle_dhee_read(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee import runtime

    runtime_result = runtime.execute_router("read", args)
    if runtime_result is not None:
        return runtime_result

    from dhee.router.handlers import handle_dhee_read
    return handle_dhee_read(args)


def _handle_dhee_bash(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee import runtime

    runtime_result = runtime.execute_router("bash", args)
    if runtime_result is not None:
        return runtime_result

    from dhee.router.handlers import handle_dhee_bash
    return handle_dhee_bash(args)


def _handle_dhee_grep(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee import runtime

    runtime_result = runtime.execute_router("grep", args)
    if runtime_result is not None:
        return runtime_result

    from dhee.router.handlers import handle_dhee_grep
    return handle_dhee_grep(args)


def _handle_dhee_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.router.handlers import handle_dhee_agent
    return handle_dhee_agent(args)


def _handle_dhee_expand_result(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.router.handlers import handle_dhee_expand_result
    return handle_dhee_expand_result(args)


def _handle_dhee_handoff(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.handoff_snapshot import build_handoff_snapshot

    repo = args.get("repo")
    if repo:
        repo = os.path.abspath(str(repo))
    return build_handoff_snapshot(
        _get_db(),
        user_id=str(args.get("user_id") or "default"),
        repo=repo,
        workspace_id=args.get("workspace_id") or repo,
        thread_id=args.get("thread_id"),
        memory_limit=_bounded_limit(args, "memory_limit", 5, 20),
        artifact_limit=_bounded_limit(args, "artifact_limit", 5, 20),
        task_limit=_bounded_limit(args, "task_limit", 5, 20),
        intention_limit=_bounded_limit(args, "intention_limit", 5, 20),
    )


def _handle_dhee_context_bootstrap(args: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.core.context_bootstrap import build_context_bootstrap

    return build_context_bootstrap(
        _get_db(),
        args,
        default_user_id=str(args.get("user_id") or os.environ.get("DHEE_USER_ID") or "default"),
        default_agent_id=_default_agent_id(args),
    )


HANDLERS = {
    "remember": _handle_remember,
    "recall": _handle_recall,
    "context": _handle_context,
    "dhee_submit_learning": _handle_dhee_submit_learning,
    "dhee_search_learnings": _handle_dhee_search_learnings,
    "dhee_promote_learning": _handle_dhee_promote_learning,
    "dhee_context_status": _handle_dhee_context_status,
    "dhee_context_state": _handle_dhee_context_state,
    "dhee_context_checkpoint": _handle_dhee_context_checkpoint,
    "dhee_context_rollover": _handle_dhee_context_rollover,
    "dhee_context_provision": _handle_dhee_context_provision,
    "dhee_scene_world_route": _handle_dhee_scene_world_route,
    "dhee_scene_compile": _handle_dhee_scene_compile,
    "dhee_scene_search": _handle_dhee_scene_search,
    "dhee_context_pack": _handle_dhee_context_pack,
    "dhee_repo_brain_index": _handle_dhee_repo_brain_index,
    "dhee_repo_brain_get": _handle_dhee_repo_brain_get,
    "dhee_repo_brain_localize": _handle_dhee_repo_brain_localize,
    "dhee_repo_graph_export": _handle_dhee_repo_graph_export,
    "dhee_context_graph_query": _handle_dhee_context_graph_query,
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
    "dhee_tools_list": _handle_dhee_tools_list,
    "dhee_shell": _handle_dhee_shell,
    "dhee_inbox": _handle_dhee_inbox,
    "dhee_broadcast": _handle_dhee_broadcast,
    "checkpoint": _handle_checkpoint,
    "dhee_read": _handle_dhee_read,
    "dhee_bash": _handle_dhee_bash,
    "dhee_grep": _handle_dhee_grep,
    "dhee_agent": _handle_dhee_agent,
    "dhee_expand_result": _handle_dhee_expand_result,
    "dhee_context_bootstrap": _handle_dhee_context_bootstrap,
    "dhee_handoff": _handle_dhee_handoff,
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
