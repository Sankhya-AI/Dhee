"""PreToolUse gate that steers native Read/Bash onto the Dhee router.

Invoked from the Claude Code PreToolUse hook. Returns one of:

    {} (or empty)                    — allow (default)
    {"permissionDecision": "deny",
     "reason": "...",
     "additionalContext": "..."}     — block and tell the model why

The gate only fires when the environment flag ``DHEE_ROUTER_ENFORCE=1``
is set. Without that flag the hook is a no-op, so non-enforce users pay
only the fork-exec cost of a hook invocation.

Heuristics — intentionally conservative (the router's own thresholds
match these, so denials are honest):

    Read:  deny when file exists and size > 20 KB
    Bash:  deny when command matches a heavy-output regex
           (git log/diff/show, grep -r, find /, ls -R, cat big, pytest
           without -q, npm test, curl, tail -f, etc.)

Small files and short-output commands pass through untouched. That's
the whole point — enforce where it pays, don't interfere where it
doesn't.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


READ_SIZE_THRESHOLD = 20 * 1024  # 20 KB
_FLAG_ENV = "DHEE_ROUTER_ENFORCE"


def _flag_file() -> Path:
    custom = os.environ.get("DHEE_ROUTER_ENFORCE_FILE")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".dhee" / "router_enforce"


# Patterns that historically produce large output. Single regex tested
# on the command string. Matched substrings shown in the deny reason.
_HEAVY_BASH_PATTERNS = [
    (re.compile(r"\bgit\s+(log|diff|show|blame)\b"), "git log/diff/show/blame"),
    (re.compile(r"\bgrep\s+[^|]*-[A-Za-z]*r"), "grep -r (recursive)"),
    # `\b` treats `-` and `.` as word boundaries, so `\bword\b` matches inside
    # `word-suffix`, `word.method`, `pkg-word`. Anchor with shell separators
    # instead so `tree-sitter`, `pkg.cargo`, `treelib`, `ripgrep_setup`, etc.
    # don't fire false positives. Each tool ends at whitespace, pipe, or EOL.
    (re.compile(r"(?:^|[\s|;&])rg(?:\s|$)"), "ripgrep"),
    (re.compile(r"(?:^|[\s|;&])find\s+[/\.]"), "find across a tree"),
    (re.compile(r"(?:^|[\s|;&])ls\s+[^|]*-[A-Za-z]*R"), "ls -R"),
    (re.compile(r"(?:^|[\s|;&])tree(?:\s|$)"), "tree"),
    (re.compile(r"(?:^|[\s|;&])pytest(?:\s|$)"), "pytest"),
    (re.compile(r"(?:^|[\s|;&])npm\s+(test|run)(?:\s|$)"), "npm test/run"),
    (re.compile(r"(?:^|[\s|;&])cargo\s+(build|test)(?:\s|$)"), "cargo build/test"),
    (re.compile(r"(?:^|[\s|;&])curl(?:\s|$)"), "curl (HTTP fetch)"),
    (re.compile(r"(?:^|[\s|;&])tail\s+-f\b"), "tail -f"),
]


def _enforce_on() -> bool:
    if os.environ.get(_FLAG_ENV) == "1":
        return True
    try:
        return _flag_file().exists()
    except Exception:
        return False


_ESCAPE_HINT = (
    " If the dhee MCP server isn't wired into this host, install it with "
    "`dhee install` (adds .mcp.json / Claude Code config), or disable "
    "enforcement for this session with `rm ~/.dhee/router_enforce` (or "
    "unset DHEE_ROUTER_ENFORCE)."
)


def _deny(reason: str, steer: str) -> dict[str, Any]:
    return {
        "permissionDecision": "deny",
        "reason": reason,
        "additionalContext": steer + _ESCAPE_HINT,
    }


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    """Decide whether to allow or deny a native tool call.

    Returns ``{}`` for allow (pass-through). Returns a deny block when
    enforcement is on and heuristics fire.
    """
    if not _enforce_on():
        return {}
    if not isinstance(payload, dict):
        return {}

    tool = payload.get("tool_name") or payload.get("tool") or ""
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool == "Read":
        return _evaluate_read(tool_input)
    if tool == "Bash":
        return _evaluate_bash(tool_input)
    if tool == "Grep":
        return _evaluate_grep(tool_input)
    return {}


def _evaluate_grep(inp: dict[str, Any]) -> dict[str, Any]:
    """Steer native Grep onto dhee_grep.

    Native Grep defaults to ``files_with_matches`` which already has a
    small footprint, but ``output_mode="content"`` or wide searches
    across the repo can dump hundreds of lines. Deny whenever the caller
    asks for content (the expensive mode) or clearly scans a whole tree.
    """
    pattern = inp.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return {}
    output_mode = inp.get("output_mode")
    path = inp.get("path") or "."
    if output_mode == "content" or (output_mode is None and inp.get("-C")):
        reason = (
            "Router enforcement: Grep output_mode=content dumps raw lines "
            "into context."
        )
        steer = (
            f"Call mcp__dhee__dhee_grep(pattern={pattern!r}, path={path!r}) "
            "instead. You get match count + top file:line hits + per-file "
            "density; full hit list stays behind a ptr."
        )
        return _deny(reason, steer)
    return {}


def _evaluate_read(inp: dict[str, Any]) -> dict[str, Any]:
    path = inp.get("file_path") or inp.get("path")
    if not isinstance(path, str) or not path:
        return {}
    # Only fire on absolute paths (Claude Code documents these as absolute)
    try:
        size = Path(path).stat().st_size
    except OSError:
        return {}  # unreadable / missing — let the native tool surface the error
    if size <= READ_SIZE_THRESHOLD:
        return {}
    # Respect an explicit small slice (offset+limit): that's already frugal.
    offset = inp.get("offset")
    limit = inp.get("limit")
    if offset is not None and limit is not None:
        try:
            if int(limit) <= 200:
                return {}
        except (TypeError, ValueError):
            pass
    reason = (
        f"Router enforcement: file is {size} bytes (> {READ_SIZE_THRESHOLD}). "
        f"Raw content would bloat context."
    )
    steer = (
        f"Call mcp__dhee__dhee_read(file_path={path!r}) instead. It returns "
        "a digest + ptr; raw stays out of the conversation. Use offset/limit "
        "for a specific range."
    )
    return _deny(reason, steer)


_QUOTED_REGION = re.compile(r"'[^']*'|\"[^\"]*\"")

# A reducer pipe bounds the producer's output. If a heavy command is
# already piped through one of these, the context blast-radius is
# capped — let it through.
_REDUCER_PIPE = re.compile(
    r"\|\s*(?:"
    r"head\s+(?:-[A-Za-z]*\s*)?-?\d+"          # | head 50, | head -n 50
    r"|tail\s+(?:-[A-Za-z]*\s*)?-?\d+"          # | tail -20
    r"|wc(?:\s|$)"                               # | wc / | wc -l
    r"|grep\s+-c\b"                              # | grep -c pattern
    r"|sort\s*(?:\|.*)?\|\s*(?:head|tail)\s"     # | sort | head
    r")"
)

# Explicit per-command bypass: the model (or user) can prepend a
# ``# dhee:bypass`` comment to opt out for one invocation. Useful when
# the command is genuinely small but matches a heuristic.
_BYPASS_TOKEN = re.compile(r"#\s*dhee\s*:\s*bypass\b")


def _strip_quoted(cmd: str) -> str:
    """Replace quoted substrings with spaces so heavy-pattern regexes
    don't misfire on strings the shell would pass as literal arguments
    (e.g. ``echo 'not a git log, just text'``)."""
    return _QUOTED_REGION.sub(lambda m: " " * len(m.group(0)), cmd)


def _is_output_bounded(cmd: str) -> bool:
    """Return True when the command pipes its producer into a bounded
    reducer (head/tail/wc/grep -c). When that's the case the heavy
    pattern can't actually flood the context."""
    return bool(_REDUCER_PIPE.search(cmd))


def _evaluate_bash(inp: dict[str, Any]) -> dict[str, Any]:
    cmd = inp.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return {}
    if _BYPASS_TOKEN.search(cmd):
        return {}
    if _is_output_bounded(cmd):
        return {}
    scan = _strip_quoted(cmd)
    for rx, label in _HEAVY_BASH_PATTERNS:
        if rx.search(scan):
            reason = f"Router enforcement: command matches heavy-output class ({label})."
            steer = (
                f"Call mcp__dhee__dhee_bash(command={cmd!r}) instead, or pipe "
                "the producer through a bounded reducer (e.g. ``| tail -50``, "
                "``| head -n 50``, ``| wc -l``). For a one-off bypass, append "
                "``# dhee:bypass`` to the command."
            )
            return _deny(reason, steer)
    return {}
