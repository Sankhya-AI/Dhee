"""Dhee Claude Code hook dispatch.

Usage::

    python -m dhee.hooks.claude_code <event_name>

Reads the Claude Code hook payload from stdin (JSON).
Writes JSON response to stdout.
On any error, outputs ``{}`` — never fails the host agent.

Events handled:
    SessionStart      — inject full Dhee context (session + memories + insights)
    UserPromptSubmit  — inject relevant memories for the current prompt
    PostToolUse       — capture tool outcomes into Dhee memory
    PreCompact        — checkpoint state, re-inject context to survive compaction
    Stop / SessionEnd — checkpoint session with outcomes
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_MAX_REMEMBER_CHARS = 2000
_MAX_QUERY_CHARS = 200


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
# Handlers — each returns a dict for stdout JSON
# ---------------------------------------------------------------------------


def handle_session_start(payload: dict[str, Any]) -> dict[str, Any]:
    dhee = _get_dhee()

    task_desc = (
        payload.get("task_description")
        or payload.get("initial_prompt")
        or payload.get("prompt")
        or ""
    )

    ctx = dhee.context(
        task_description=task_desc or None,
        user_id=os.environ.get("DHEE_USER_ID", "default"),
    )

    if not ctx:
        return {}

    has_content = (
        ctx.get("memories")
        or ctx.get("last_session")
        or ctx.get("insights")
        or ctx.get("intentions")
        or ctx.get("performance")
    )
    if not has_content:
        return {}

    xml = _render(ctx, task_description=task_desc or None)
    return {"systemMessage": xml}


def handle_user_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        prompt = str(payload.get("prompt", payload.get("content", "")))
    elif isinstance(payload, str):
        prompt = payload
    else:
        prompt = str(payload)

    if not prompt.strip():
        return {}

    dhee = _get_dhee()
    results = dhee.recall(query=prompt[:_MAX_QUERY_CHARS], limit=5)
    if not results:
        return {}

    xml = _render({"memories": results}, max_tokens=500)
    return {"systemMessage": xml}


def handle_post_tool(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    tool_result = payload.get("tool_result", "")
    success = payload.get("success", True)

    if not tool_name:
        return {}

    write_tools = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
    shell_tools = {"Bash", "BashOutput"}
    if tool_name not in write_tools and tool_name not in shell_tools:
        return {}

    if tool_name in write_tools:
        path = ""
        if isinstance(tool_input, dict):
            path = tool_input.get("file_path", tool_input.get("path", ""))
        content = f"edited {path}" if path else f"used {tool_name}"
        if not success:
            content = f"failed to edit {path}: {str(tool_result)[:100]}"
    else:
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = tool_input.get("command", "")[:150]
        content = f"ran: {cmd}" if cmd else f"used {tool_name}"
        if not success:
            stderr = str(tool_result)[:200] if tool_result else "unknown error"
            content = f"command failed: {cmd[:80]} — {stderr}"

    from dhee.hooks.claude_code.privacy import filter_secrets

    content = filter_secrets(content)
    if len(content) < 10:
        return {}

    try:
        dhee = _get_dhee()
        dhee.remember(
            content=content[:_MAX_REMEMBER_CHARS],
            metadata={"source": "claude_code_hook", "tool": tool_name, "success": success},
        )
    except Exception:
        pass

    return {}


def handle_pre_compact(payload: dict[str, Any]) -> dict[str, Any]:
    dhee = _get_dhee()

    summary = "session compacted"
    if isinstance(payload, dict):
        summary = payload.get("summary", summary)

    try:
        dhee.checkpoint(summary=summary, status="compacted")
    except Exception:
        pass

    ctx = dhee.context(user_id=os.environ.get("DHEE_USER_ID", "default"))
    if not ctx:
        return {}
    xml = _render(ctx)
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
