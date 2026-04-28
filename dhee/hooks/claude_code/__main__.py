"""Dhee Claude Code hook dispatch.

Usage::

    python -m dhee.hooks.claude_code <event_name>

Reads the Claude Code hook payload from stdin (JSON).
Writes JSON response to stdout.
On any error, outputs ``{}`` — never fails the host agent.

v3.3.1 architecture: Dhee owns the information flow into the LLM.

    SessionStart  — auto-ingest stale docs + assemble full context
                    (relevant doc chunks + typed cognition). Inject only
                    when there's real signal.
    UserPromptSubmit — search doc chunks for THIS specific prompt.
                    Inject only high-confidence matches. No raw memory
                    recall (that was the v3.3.0 noise source).
    PostToolUse   — store genuine signal only: bash failures, file edits.
    PreCompact    — checkpoint + re-inject context to survive compaction.
    Stop/End      — checkpoint with typed outcomes.

The assembler (not the renderer, not the hooks) decides what enters each
LLM call. The renderer just formats the assembler's decisions as XML.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_MAX_REMEMBER_CHARS = 2000


def _get_dhee():
    from dhee import Dhee

    return Dhee(
        user_id=os.environ.get("DHEE_USER_ID", "default"),
        auto_context=False,
        auto_checkpoint=False,
    )


def _maybe_tail_ingest_gstack(dhee: Any) -> None:
    """Best-effort gstack delta ingest on session start/stop.

    No-op unless the user explicitly ran ``dhee install gstack``. Never
    raises — runs inside Claude Code hooks.
    """

    try:
        from dhee.cli_config import load_config

        config = load_config() or {}
        harnesses = config.get("harnesses") or {}
        gstack_cfg = harnesses.get("gstack") or {}
        if not gstack_cfg.get("enabled"):
            return
    except Exception:
        return

    try:
        from dhee.adapters import gstack as gstack_adapter

        gstack_adapter.tail_ingest(dhee=dhee)
    except Exception:
        return


def _render(ctx: dict[str, Any], **kwargs: Any) -> str:
    from dhee.hooks.claude_code.renderer import render_context

    return render_context(ctx, **kwargs)


def _artifact_manager(dhee: Any):
    from dhee.core.artifacts import ArtifactManager

    return ArtifactManager(dhee._engram.memory.db, engram=dhee._engram)


def _shared_snapshot(dhee: Any) -> dict[str, Any]:
    from dhee.core.shared_tasks import shared_task_snapshot

    try:
        return shared_task_snapshot(
            dhee._engram.memory.db,
            user_id=os.environ.get("DHEE_USER_ID", "default"),
            repo=os.getcwd(),
            workspace_id=os.getcwd(),
            limit=5,
        )
    except Exception:
        return {"task": None, "results": []}


def _hook_cwd(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("cwd", "workspace", "repo", "project_dir"):
            value = str(payload.get(key) or "").strip()
            if value:
                return os.path.abspath(os.path.expanduser(value))
    return os.getcwd()


def _repo_context_for(cwd: str, *, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Pull shared entries from the linked repo containing *cwd*.

    Returns ``[]`` when the cwd isn't under a linked repo or repo_link
    isn't importable. Never raises into the hook host.
    """
    if not query or not str(query).strip():
        return []
    try:
        from dhee import repo_link

        return repo_link.search_entries(query, cwd=cwd, limit=limit)
    except Exception:
        return []


def _discover_repo_config(start: str) -> dict[str, Any]:
    """Find public .dhee/config.json for repo-link context."""
    try:
        root = Path(start).resolve()
    except Exception:
        root = Path.cwd()
    home = Path.home().resolve()
    for candidate in [root, *root.parents]:
        if candidate == home:
            break
        cfg = candidate / ".dhee" / "config.json"
        if not cfg.is_file():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if isinstance(data, dict):
            os.environ.setdefault("DHEE_REPO_ROOT", str(candidate))
            return {"repo_root": str(candidate), **data}
    return {}


def _repo_last_session(repo: str) -> dict[str, Any] | None:
    try:
        from dhee.core.kernel import get_last_session

        session = get_last_session(
            agent_id="claude-code",
            repo=repo,
            fallback_log_recovery=True,
            user_id=os.environ.get("DHEE_USER_ID", "default"),
            requester_agent_id="claude-code-hook",
        )
        return session if isinstance(session, dict) else None
    except Exception:
        return None


# Heading-breadcrumb fragments that mark CLAUDE.md/system-prompt material:
# style guides, commit conventions, harness boilerplate. Per-turn injection
# should carry signal about *this* prompt, not the repo's coding style — that
# belongs in the always-on system context.
_STYLE_HEADINGS = (
    "coding style",
    "naming convention",
    "commit",
    "pull request guidelines",
    "engram continuity",
    "repository guidelines",
)


def _is_style_chunk(match: Any) -> bool:
    head = (getattr(match, "heading_breadcrumb", "") or "").lower()
    if not head:
        return False
    return any(needle in head for needle in _STYLE_HEADINGS)


def _filter_style_chunks(matches: list) -> list:
    return [m for m in (matches or []) if not _is_style_chunk(m)]


def _looks_like_continue(prompt: str) -> bool:
    p = (prompt or "").strip().lower()
    if not p:
        return False
    return any(
        phrase in p
        for phrase in (
            "continue",
            "resume",
            "pick up",
            "where we left",
            "same repo",
            "last session",
            "previous session",
        )
    )


_SHARED_RELEVANCE_THRESHOLD = float(
    os.environ.get("DHEE_SHARED_RELEVANCE_THRESHOLD", "0.50") or 0.50
)


def _cosine(a, b) -> float:
    import math

    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        x = float(a[i])
        y = float(b[i])
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _shared_block_is_relevant(
    dhee: Any,
    prompt: str,
    shared: dict[str, Any],
    *,
    threshold: float = _SHARED_RELEVANCE_THRESHOLD,
) -> bool:
    # Per-turn semantic gate on the <shared> block. Mirrors the doc-chunk
    # gate at assembler.py:_assemble_docs_only — if the active shared task
    # has nothing to do with what the user just asked, drop it rather than
    # injecting a stale title with an empty result feed.
    task = shared.get("task")
    if not task:
        return False

    title = str(task.get("title") or "").strip()
    last_digest = ""
    for r in shared.get("results") or []:
        d = str(r.get("digest") or "")
        if d:
            last_digest = d[:200]
            break
    summary = (title + " " + last_digest).strip()
    if not summary:
        return False
    if not prompt or not prompt.strip():
        return False

    try:
        embedder = dhee.memory.embedder
        v_prompt = embedder.embed(prompt, memory_action="search")
        v_task = embedder.embed(summary, memory_action="search")
    except Exception:
        # Fail closed — better to drop than re-emit the noise that motivated
        # this gate in the first place.
        return False

    return _cosine(v_prompt, v_task) >= threshold


def _merge_doc_matches(*groups: list[Any]) -> list[Any]:
    seen: set[tuple[str, int, str]] = set()
    merged: list[Any] = []
    for group in groups:
        for item in group or []:
            key = (
                str(getattr(item, "source_path", "")),
                int(getattr(item, "chunk_index", 0)),
                str(getattr(item, "text", ""))[:120],
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def handle_session_start(payload: dict[str, Any]) -> dict[str, Any]:
    from dhee.hooks.claude_code.assembler import assemble
    from dhee.hooks.claude_code.ingest import auto_ingest_project

    repo_cfg = _discover_repo_config(_hook_cwd(payload))
    repo_root = str(repo_cfg.get("repo_root") or _hook_cwd(payload))

    dhee = _get_dhee()

    task_desc = ""
    if isinstance(payload, dict):
        task_desc = (
            payload.get("task_description")
            or payload.get("initial_prompt")
            or payload.get("prompt")
            or ""
        )

    # Auto-ingest any stale project docs (CLAUDE.md, AGENTS.md, etc.).
    # SHA check makes this a no-op if files haven't changed.
    try:
        auto_ingest_project(dhee)
    except Exception:
        pass

    _maybe_tail_ingest_gstack(dhee)

    # Assemble: doc chunks + typed cognition, budgeted.
    assembled = assemble(dhee, query=task_desc, include_cognition=True)
    artifact_matches = []
    if task_desc:
        try:
            artifact_matches = _artifact_manager(dhee).prompt_matches(
                task_desc,
                user_id=os.environ.get("DHEE_USER_ID", "default"),
                cwd=os.getcwd(),
                limit=3,
            )
        except Exception:
            artifact_matches = []
    doc_matches = _merge_doc_matches(artifact_matches, assembled.doc_matches)
    shared = _shared_snapshot(dhee)
    router_on = os.environ.get("DHEE_ROUTER") == "1"
    typed = dict(assembled.typed_cognition or {})
    # Repo config should bind local shared-context identity silently, but it must not
    # inject a prior transcript into every fresh session. Continuity is
    # expensive context, so fetch it when the user asks to continue/resume or
    # when an admin explicitly enables automatic continuity.
    should_auto_resume = _looks_like_continue(task_desc) or os.environ.get("DHEE_AUTO_CONTINUITY") == "1"
    if should_auto_resume and not typed.get("last_session"):
        last = _repo_last_session(repo_root)
        if last:
            typed["last_session"] = last

    repo_entries = _repo_context_for(repo_root, query=task_desc, limit=5)

    if (
        not doc_matches
        and not router_on
        and not shared.get("task")
        and not typed.get("last_session")
        and not assembled.has_cognition
        and not repo_entries
    ):
        return {}

    xml = _render(
        typed,
        task_description=task_desc or None,
        doc_matches=doc_matches,
        shared_task=shared.get("task"),
        shared_task_results=shared.get("results") or [],
        repo_entries=repo_entries,
    )
    if not xml:
        return {}
    return {"systemMessage": xml}


def handle_user_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    """Per-turn enrichment.

    Goal: every prompt arrives with the signal it needs — recent edits
    in this session, last session for this repo, top personal-memory
    hits, and high-confidence doc chunks for *this* specific prompt.
    Style guides and harness boilerplate are filtered out (CLAUDE.md
    territory, not per-turn). Off-topic prompts inject nothing.
    """
    from dhee.hooks.claude_code.assembler import assemble, assemble_docs_only

    if isinstance(payload, dict):
        prompt = str(payload.get("prompt", payload.get("content", "")))
    elif isinstance(payload, str):
        prompt = payload
    else:
        prompt = str(payload)

    if not prompt.strip():
        return {}

    dhee = _get_dhee()
    _discover_repo_config(_hook_cwd(payload))

    # ── Doc chunks: gated, style-filtered ────────────────────────────
    doc_matches = assemble_docs_only(dhee, query=prompt)
    artifact_matches: list = []
    try:
        artifact_matches = _artifact_manager(dhee).prompt_matches(
            prompt,
            user_id=os.environ.get("DHEE_USER_ID", "default"),
            cwd=os.getcwd(),
            limit=3,
        )
    except Exception:
        artifact_matches = []
    doc_matches = _merge_doc_matches(artifact_matches, doc_matches)
    doc_matches = _filter_style_chunks(doc_matches)

    # ── Cognition: memories, insights, beliefs, policies ─────────────
    # Tight per-turn budget — UserPromptSubmit fires on every message,
    # so we cap aggressively. The renderer trims further by priority.
    typed_cognition: dict[str, Any] = {}
    try:
        assembled = assemble(
            dhee,
            query=prompt,
            doc_budget_tokens=0,           # docs already pulled above
            cognition_budget_tokens=500,
            include_cognition=True,
        )
        typed_cognition = assembled.typed_cognition or {}
    except Exception:
        typed_cognition = {}

    # ── Edit ledger: this session's file changes (always on) ─────────
    edits_block = ""
    try:
        from dhee.router.edit_ledger import render_block as _render_edits

        edits_block = _render_edits()
    except Exception:
        edits_block = ""

    # ── Last session for this repo: continuity even without "continue" ──
    repo = os.environ.get("DHEE_REPO_ROOT") or os.getcwd()
    last_session = _repo_last_session(repo)
    if last_session:
        typed_cognition.setdefault("last_session", last_session)

    # ── Shared cross-session task (semantic gate already applied) ────
    shared = _shared_snapshot(dhee)
    if shared.get("task") and not _shared_block_is_relevant(dhee, prompt, shared):
        shared = {"task": None, "results": []}

    repo_entries = _repo_context_for(repo, query=prompt, limit=3)

    has_signal = (
        bool(doc_matches)
        or bool(edits_block)
        or bool(typed_cognition)
        or bool(shared.get("task"))
        or bool(repo_entries)
    )
    if not has_signal:
        return {}

    # Per-turn caps: aggressive trimming. SessionStart and PreCompact
    # use the renderer's defaults (1500 tokens, 8 memories) — those fire
    # rarely. UserPromptSubmit fires every turn, so the budget is half.
    xml = _render(
        typed_cognition,
        task_description=prompt,
        max_tokens=900,
        max_memories=3,
        max_insights=3,
        max_intentions=2,
        doc_matches=doc_matches,
        edits_block=edits_block or None,
        shared_task=shared.get("task"),
        shared_task_results=shared.get("results") or [],
        repo_entries=repo_entries,
    )
    if not xml:
        return {}
    return {"systemMessage": xml}


def handle_pre_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Router enforcement gate. No-op unless DHEE_ROUTER_ENFORCE=1."""
    from dhee.router.pre_tool_gate import evaluate

    if isinstance(payload, dict):
        try:
            tool_name = str(payload.get("tool_name") or "")
            tool_input = payload.get("tool_input", {}) or {}
            if tool_name == "Read" and isinstance(tool_input, dict):
                path = str(tool_input.get("file_path") or tool_input.get("path") or "").strip()
                if path:
                    dhee = _get_dhee()
                    _artifact_manager(dhee).attach(
                        path,
                        user_id=os.environ.get("DHEE_USER_ID", "default"),
                        cwd=os.getcwd(),
                        harness="claude_code",
                        binding_source="artifact_attached",
                        metadata={"tool_name": tool_name},
                    )
        except Exception:
            pass

    try:
        return evaluate(payload) or {}
    except Exception:
        return {}


def handle_post_tool(payload: dict[str, Any]) -> dict[str, Any]:
    from dhee.core.artifacts import extract_text_from_host_payload
    from dhee.hooks.claude_code.signal import extract_signal

    if not isinstance(payload, dict):
        return {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    tool_result = payload.get("tool_response", payload.get("tool_result", ""))
    success = payload.get("success", True)

    if success and tool_name == "Read" and isinstance(tool_input, dict):
        try:
            path = str(tool_input.get("file_path") or tool_input.get("path") or "").strip()
            extracted_text = extract_text_from_host_payload(tool_result).strip()
            if path and extracted_text:
                dhee = _get_dhee()
                _artifact_manager(dhee).capture_host_parse(
                    path=path,
                    extracted_text=extracted_text,
                    user_id=os.environ.get("DHEE_USER_ID", "default"),
                    cwd=os.getcwd(),
                    harness="claude_code",
                    extraction_source="claude_read",
                    extraction_version="host-v1",
                    metadata={
                        "tool_name": tool_name,
                        "pages": tool_input.get("pages"),
                        "offset": tool_input.get("offset"),
                        "limit": tool_input.get("limit"),
                    },
                )
        except Exception:
            pass

    # Phase 7: record successful edits into the per-session ledger for
    # PreCompact dedup. Best-effort, never fails the hook.
    if success and tool_name in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        try:
            from dhee.router.edit_ledger import record as _record_edit

            path = ""
            new_content = ""
            if isinstance(tool_input, dict):
                path = str(tool_input.get("file_path") or tool_input.get("path") or "")
                new_content = str(
                    tool_input.get("new_string")
                    or tool_input.get("content")
                    or tool_input.get("new_source")
                    or ""
                )
            if path:
                _record_edit(tool_name, path, new_content)
        except Exception:
            pass

    signal = extract_signal(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_result=tool_result,
        success=success,
    )
    if signal is None:
        return {}

    content, metadata = signal
    metadata = {"source": "claude_code_hook", **metadata}

    try:
        dhee = _get_dhee()
        dhee.remember(
            content=content[:_MAX_REMEMBER_CHARS],
            metadata=metadata,
        )
    except Exception:
        pass

    # Fan this signal onto the workspace information line so sibling
    # agents (codex, browser, etc.) in the same workspace see the
    # event live. Idempotent; safe even if the router already emitted.
    try:
        from dhee.core.workspace_line import emit_agent_activity

        source_path = str(
            (tool_input or {}).get("file_path")
            or (tool_input or {}).get("path")
            or (tool_input or {}).get("notebook_path")
            or ""
        ) or None
        ptr = str(metadata.get("ptr") or "") or None
        session_id = (
            payload.get("session_id")
            or payload.get("native_session_id")
            or os.environ.get("CLAUDE_SESSION_ID")
            or os.environ.get("DHEE_SESSION_ID")
        )
        tool_use_id = str(payload.get("tool_use_id") or "") or None
        dhee = _get_dhee()
        emit_agent_activity(
            dhee._engram.memory.db,
            user_id=os.environ.get("DHEE_USER_ID", "default"),
            tool_name=str(tool_name or "tool"),
            packet_kind="hook_post_tool",
            digest=content,
            runtime_id="claude-code",
            native_session_id=session_id,
            session_id=session_id,
            cwd=os.getcwd(),
            repo=os.getcwd(),
            source_path=source_path,
            source_event_id=tool_use_id,
            ptr=ptr,
            harness="claude-code",
            agent_id="claude-code",
            metadata=metadata,
            result_status="completed" if success else "failed",
        )
    except Exception:
        pass

    return {}


def handle_pre_compact(payload: dict[str, Any]) -> dict[str, Any]:
    from dhee.hooks.claude_code.assembler import assemble

    dhee = _get_dhee()

    summary = "session compacted"
    if isinstance(payload, dict):
        summary = payload.get("summary", summary)

    try:
        dhee.checkpoint(summary=summary, status="compacted")
    except Exception:
        pass

    # Re-inject context to survive compaction.
    assembled = assemble(dhee, query=summary, include_cognition=True)

    # Phase 7: deduped edit ledger fed as a first-class renderer section.
    edits_block = ""
    try:
        from dhee.router.edit_ledger import render_block as _edits_block

        edits_block = _edits_block()
    except Exception:
        edits_block = ""

    shared = _shared_snapshot(dhee)
    if assembled.is_empty and not edits_block and not shared.get("task"):
        return {}

    xml = _render(
        assembled.typed_cognition,
        doc_matches=assembled.doc_matches,
        edits_block=edits_block or None,
        shared_task=shared.get("task"),
        shared_task_results=shared.get("results") or [],
    )
    if not xml:
        return {}
    return {"systemMessage": xml}


def handle_stop(payload: dict[str, Any]) -> dict[str, Any]:
    dhee = _get_dhee()

    _maybe_tail_ingest_gstack(dhee)

    summary = "session ended"
    task_type = None
    outcome_score = None
    what_worked = None
    what_failed = None

    if isinstance(payload, dict):
        summary = payload.get("summary", payload.get("task_description", summary))
        task_type = payload.get("task_type")
        if payload.get("outcome_score") is not None:
            try:
                outcome_score = float(payload["outcome_score"])
            except (TypeError, ValueError):
                pass
        what_worked = payload.get("what_worked")
        what_failed = payload.get("what_failed")

    try:
        dhee.checkpoint(
            summary=summary,
            task_type=task_type,
            outcome_score=outcome_score,
            what_worked=what_worked,
            what_failed=what_failed,
            status="completed",
            repo=os.getcwd(),
        )
    except Exception:
        pass

    # M4.3 — Nididhyasana session-boundary scheduler. Fires the readiness
    # gate and logs the decision so `dhee doctor` can show when evolution
    # would have triggered. Full training cycle stays behind force_evolve.
    try:
        memory = getattr(dhee, "_memory", None) or getattr(dhee, "memory", None)
        evo = getattr(memory, "evolution_layer", None) if memory else None
        if evo is not None:
            outcome_meta = {}
            if isinstance(payload, dict):
                for key in ("tests_passed", "tests_failed", "correction_count", "reverted"):
                    if key in payload:
                        outcome_meta[key] = payload.get(key)
                raw_signals = payload.get("signals")
                if isinstance(raw_signals, dict):
                    # Preserve session-level heuristics emitted by the tracker.
                    outcome_meta.update(raw_signals)
            if any(
                value is not None and value != ""
                for value in (task_type, outcome_score, what_worked, what_failed)
            ) or outcome_meta:
                evo.record_task_outcome(
                    task_type=task_type,
                    outcome_score=outcome_score,
                    what_worked=what_worked,
                    what_failed=what_failed,
                    metadata=outcome_meta,
                    source="claude_stop",
                )
            evo.on_session_end(reason=payload.get("hook_event_name", "session_end"))
    except Exception:
        pass

    return {}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_HANDLERS = {
    "SessionStart": handle_session_start,
    "UserPromptSubmit": handle_user_prompt,
    "PreToolUse": handle_pre_tool,
    "PostToolUse": handle_post_tool,
    "PreCompact": handle_pre_compact,
    "Stop": handle_stop,
    "SessionEnd": handle_stop,
}


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: python -m dhee.hooks.claude_code <event>\n")
        sys.stdout.write("{}\n")
        return 1

    event = sys.argv[1]
    handler = _HANDLERS.get(event)
    if not handler:
        sys.stdout.write("{}\n")
        return 0

    try:
        raw = sys.stdin.read() or "{}"
    except Exception:
        raw = "{}"

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"prompt": raw, "raw": raw}

    try:
        result = handler(payload)
        sys.stdout.write(json.dumps(result or {}) + "\n")
    except Exception as exc:
        sys.stderr.write(f"dhee hook {event}: {exc}\n")
        sys.stdout.write("{}\n")
        return 0

    # PreToolUse deny signalling: emit JSON + exit 2 per Claude Code docs.
    # Mirror the deny reason to stderr so the harness logs a readable line
    # instead of "PreToolUse:Bash hook error: ... No stderr output" — empty
    # stderr + exit 2 is misclassified as a crash by Claude Code.
    if (
        event == "PreToolUse"
        and isinstance(result, dict)
        and result.get("permissionDecision") == "deny"
    ):
        reason = str(result.get("reason") or "router enforcement").strip()
        sys.stderr.write(f"dhee router deny: {reason}\n")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
