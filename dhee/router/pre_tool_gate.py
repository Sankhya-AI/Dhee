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
import json
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


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _candidate_repo(inp: dict[str, Any]) -> Path:
    raw = inp.get("cwd") or inp.get("file_path") or inp.get("path") or os.getcwd()
    path = Path(str(raw or ".")).expanduser()
    if not path.is_absolute():
        path = Path(os.getcwd()) / path
    if path.suffix or (path.exists() and path.is_file()):
        path = path.parent
    path = path.resolve()
    for current in [path, *path.parents]:
        if (current / ".git").exists() or (current / ".dhee").exists():
            return current
    return path


def _fallback_enforcement_mode(inp: dict[str, Any]) -> str:
    if _truthy(os.environ.get("DHEE_REQUIRE_ACTIVE_CONTRACT")):
        return "deny"
    repo = _candidate_repo(inp)
    policy_path = repo / ".dhee" / "context" / "task_runs" / "enforcement.json"
    if not policy_path.exists():
        return "off"
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return "deny"
    mode = str((data if isinstance(data, dict) else {}).get("mode") or "").strip().lower()
    return mode if mode in {"off", "warn", "deny"} else "deny"


def _enforcement_mode_for_input(inp: dict[str, Any]) -> str:
    if _truthy(os.environ.get("DHEE_REQUIRE_ACTIVE_CONTRACT")):
        return "deny"
    try:
        from dhee.contract_runtime import contract_enforcement_status

        return str(contract_enforcement_status(repo=str(_candidate_repo(inp))).get("mode") or "off")
    except Exception:
        return _fallback_enforcement_mode(inp)


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    """Decide whether to allow or deny a native tool call.

    Returns ``{}`` for allow (pass-through). Returns a deny block when
    enforcement is on and heuristics fire.
    """
    if not isinstance(payload, dict):
        return {}

    tool = payload.get("tool_name") or payload.get("tool") or ""
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    contract_denial = _evaluate_contract_supervisor(str(tool), tool_input)
    if contract_denial:
        return contract_denial

    if not _enforce_on():
        return {}

    if tool == "Read":
        return _evaluate_read(tool_input)
    if tool == "Bash":
        return _evaluate_bash(tool_input)
    if tool == "Grep":
        return _evaluate_grep(tool_input)
    return {}


def _evaluate_contract_supervisor(tool: str, inp: dict[str, Any]) -> dict[str, Any]:
    if tool not in {"Read", "Bash", "Grep", "Edit", "Write", "MultiEdit", "NotebookEdit"}:
        return {}
    try:
        from dhee.contract_runtime import guard_router_call, router_refusal

        guard = guard_router_call(tool, inp)
        if guard.get("allowed", True):
            return {}
        refusal = router_refusal(guard)
        codes = ", ".join(refusal.get("violation_codes") or [])
        reason = f"Contract supervisor denied {tool}: {codes or refusal.get('message')}"
        steer = (
            "Activate a task contract with `dhee context task activate <task_id>` "
            "or satisfy the compiled proof obligations before retrying. "
            f"Violation codes: {codes or 'none'}. "
            f"Decision: {json.dumps(refusal, sort_keys=True, default=str)[:1200]}"
        )
        return _deny(reason, steer)
    except Exception as exc:
        if _enforcement_mode_for_input(inp) == "deny":
            reason = f"Contract supervisor unavailable for {tool}: {type(exc).__name__}"
            steer = (
                "Dhee contract enforcement is in deny mode, so native coding tools "
                "cannot run while the supervisor is unavailable. "
                f"Violation codes: CONTRACT_SUPERVISOR_UNAVAILABLE. Error: {exc}"
            )
            return _deny(reason, steer)
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
        "for a specific range; bounded Dhee reads include source_window inline "
        "even when dhee_expand_result is not available."
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
