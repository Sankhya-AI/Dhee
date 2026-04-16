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


def _render(ctx: dict[str, Any], **kwargs: Any) -> str:
    from dhee.hooks.claude_code.renderer import render_context

    return render_context(ctx, **kwargs)


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

    # Assemble: doc chunks + typed cognition, budgeted.
    assembled = assemble(dhee, query=task_desc, include_cognition=True)
    if assembled.is_empty:
        return {}

    xml = _render(
        assembled.typed_cognition,
        task_description=task_desc or None,
        doc_matches=assembled.doc_matches,
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
    if not matches:
        return {}

    xml = _render({}, doc_matches=matches)
    if not xml:
        return {}
    return {"systemMessage": xml}


def handle_post_tool(payload: dict[str, Any]) -> dict[str, Any]:
    from dhee.hooks.claude_code.signal import extract_signal

    if not isinstance(payload, dict):
        return {}

    signal = extract_signal(
        tool_name=payload.get("tool_name", ""),
        tool_input=payload.get("tool_input", {}),
        tool_result=payload.get("tool_result", ""),
        success=payload.get("success", True),
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
    if assembled.is_empty:
        return {}
    xml = _render(
        assembled.typed_cognition,
        doc_matches=assembled.doc_matches,
    )
    if not xml:
        return {}
    return {"systemMessage": xml}


def handle_stop(payload: dict[str, Any]) -> dict[str, Any]:
    dhee = _get_dhee()

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

    return {}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_HANDLERS = {
    "SessionStart": handle_session_start,
    "UserPromptSubmit": handle_user_prompt,
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


if __name__ == "__main__":
    raise SystemExit(main())
