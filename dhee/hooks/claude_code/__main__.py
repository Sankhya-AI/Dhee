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
    if assembled.is_empty and not doc_matches and not router_on and not shared.get("task"):
        return {}

    xml = _render(
        assembled.typed_cognition,
        task_description=task_desc or None,
        doc_matches=doc_matches,
        shared_task=shared.get("task"),
        shared_task_results=shared.get("results") or [],
    )
    if not xml:
        return {}
    return {"systemMessage": xml}


def handle_user_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    """Per-turn doc-chunk injection.

    Searches ingested docs for chunks relevant to THIS specific prompt.
    Only injects when there's a high-confidence match (score ≥ 0.60).
    No raw memory recall — that was the v3.3.0 noise source.

    This is where Dhee saves the most tokens: instead of the host
    carrying 2000 tokens of CLAUDE.md context every turn, Dhee injects
    ~200 tokens of the specific instructions that apply to what the
    user just asked.
    """
    from dhee.hooks.claude_code.assembler import assemble_docs_only

    if isinstance(payload, dict):
        prompt = str(payload.get("prompt", payload.get("content", "")))
    elif isinstance(payload, str):
        prompt = payload
    else:
        prompt = str(payload)

    if not prompt.strip():
        return {}

    dhee = _get_dhee()
    matches = assemble_docs_only(dhee, query=prompt)
    artifact_matches = []
    try:
        artifact_matches = _artifact_manager(dhee).prompt_matches(
            prompt,
            user_id=os.environ.get("DHEE_USER_ID", "default"),
            cwd=os.getcwd(),
            limit=3,
        )
    except Exception:
        artifact_matches = []
    matches = _merge_doc_matches(artifact_matches, matches)
    shared = _shared_snapshot(dhee)
    if not matches and not shared.get("task"):
        return {}

    xml = _render(
        {},
        doc_matches=matches,
        shared_task=shared.get("task"),
        shared_task_results=shared.get("results") or [],
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
    if (
        event == "PreToolUse"
        and isinstance(result, dict)
        and result.get("permissionDecision") == "deny"
    ):
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
