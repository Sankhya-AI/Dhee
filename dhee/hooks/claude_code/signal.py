"""Signal extraction for Claude Code PostToolUse hook.

The old v3.3.0 hook stored every Bash/Edit/Write invocation verbatim. That
polluted the memory store with transport ("ran: ls -la") instead of signal
("tests pass after fix"). Recall surfaced bash echoes with high cosine
similarity to any query containing a word that appeared in a command.

This module decides, given a tool invocation, whether it carries learnable
signal and — if so — returns the canonical form that should be stored.

Rules (honest, conservative):
- Bash success=True    → NO signal. Commands that ran fine are transport.
- Bash success=False   → signal: "bash failed: <cmd> — <stderr>"
- Edit/Write success=T → signal: "edited <path>"
- Edit/Write success=F → signal: "failed to edit <path>: <err>"
- Self-referential commands (dhee/sqlite3 ~/.dhee/hook-testing) → dropped
- Empty/tiny content   → dropped

Returns ``None`` when there is no storable signal. The caller must honor it:
``None`` means do not call ``dhee.remember()``.
"""

from __future__ import annotations

import re
from typing import Any

from dhee.hooks.claude_code.privacy import filter_secrets

_MAX_REMEMBER_CHARS = 2000
_MIN_SIGNAL_CHARS = 10

_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_SHELL_TOOLS = frozenset({"Bash", "BashOutput"})

# Commands we never want to learn from — they are Dhee's own plumbing
# or hook-testing harness. Storing them creates a pollution loop where
# recall surfaces prior recall invocations.
_SELF_REF_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bdhee\b", re.IGNORECASE),
    re.compile(r"~/\.dhee\b"),
    re.compile(r"sqlite_vec\.db"),
    re.compile(r"handoff\.db"),
    re.compile(r"\.claude/settings\.json"),
    re.compile(r"-m\s+dhee\.hooks\.claude_code"),
    # echo '{...}' | ... — typical hook-testing shape
    re.compile(r"echo\s+'\{[^']*\}'"),
    re.compile(r'echo\s+"\{[^"]*\}"'),
)


def is_self_referential(command: str) -> bool:
    """True when the command is Dhee-internal or hook-testing plumbing."""
    if not command:
        return False
    for pattern in _SELF_REF_PATTERNS:
        if pattern.search(command):
            return True
    return False


def extract_signal(
    tool_name: str,
    tool_input: Any,
    tool_result: Any,
    success: bool,
) -> tuple[str, dict[str, Any]] | None:
    """Return (content, metadata) if the invocation carries signal, else None.

    ``content`` is guaranteed to be privacy-filtered and ≥ _MIN_SIGNAL_CHARS.
    ``metadata`` describes the signal shape (kind, tool, and any identifier).
    """
    if not tool_name:
        return None

    if tool_name in _WRITE_TOOLS:
        return _write_signal(tool_name, tool_input, tool_result, success)

    if tool_name in _SHELL_TOOLS:
        return _shell_signal(tool_name, tool_input, tool_result, success)

    # Read, Grep, Glob, WebFetch, MCP, etc. carry no persistent signal.
    return None


def _write_signal(
    tool_name: str,
    tool_input: Any,
    tool_result: Any,
    success: bool,
) -> tuple[str, dict[str, Any]] | None:
    path = ""
    if isinstance(tool_input, dict):
        path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if not path:
        return None

    if success:
        content = f"edited {path}"
        kind = "file_touched"
    else:
        err = str(tool_result or "")[:200]
        content = f"failed to edit {path}: {err}" if err else f"failed to edit {path}"
        kind = "failure"

    content = filter_secrets(content)
    if len(content) < _MIN_SIGNAL_CHARS:
        return None
    return content[:_MAX_REMEMBER_CHARS], {
        "kind": kind,
        "tool": tool_name,
        "path": path,
        "success": success,
    }


def _shell_signal(
    tool_name: str,
    tool_input: Any,
    tool_result: Any,
    success: bool,
) -> tuple[str, dict[str, Any]] | None:
    cmd = ""
    if isinstance(tool_input, dict):
        cmd = str(tool_input.get("command", ""))
    if not cmd:
        return None

    if is_self_referential(cmd):
        return None

    # Successful shell commands are transport, not signal. Skip them entirely.
    # The session transcript already records what ran; storing it again just
    # pollutes recall.
    if success:
        return None

    stderr = str(tool_result or "")[:200].strip()
    cmd_short = cmd[:80].strip()
    if stderr:
        content = f"bash failed: {cmd_short} — {stderr}"
    else:
        content = f"bash failed: {cmd_short}"

    content = filter_secrets(content)
    if len(content) < _MIN_SIGNAL_CHARS:
        return None
    return content[:_MAX_REMEMBER_CHARS], {
        "kind": "failure",
        "tool": tool_name,
        "success": False,
    }


# Typed-cognition signal check for injection gating.
#
# The v3.3.0 SessionStart injected whenever ``memories`` was non-empty. But
# memories alone — under the old PostToolUse — were bash echoes, so the model
# got "ran: ls" presented as ground truth. Real cognition lives in the
# beliefs/insights/intentions/policies/last_session/performance layers, which
# are populated by checkpoint-driven reflection. Only those qualify.
_COGNITION_KEYS = (
    "last_session",
    "insights",
    "intentions",
    "performance",
    "beliefs",
    "policies",
    "warnings",
)


def has_cognition_signal(ctx: dict[str, Any]) -> bool:
    """True when ``ctx`` contains typed cognition worth injecting.

    Raw memories and episodes don't qualify — they're observations, not
    distilled learning. Injecting them every turn costs tokens and trains
    the model to expect noise under the <dhee-context> header.
    """
    if not isinstance(ctx, dict):
        return False
    for key in _COGNITION_KEYS:
        value = ctx.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and any(value):
            return True
    return False
