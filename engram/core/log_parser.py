"""Parse Claude Code .jsonl conversation logs for handoff context recovery.

Stdlib-only — no external dependencies.  Reads the structured JSONL logs that
Claude Code writes to ``~/.claude/projects/<escaped-path>/<session-id>.jsonl``
and extracts a handoff-like digest (task summary, files touched, commands run,
timestamps, etc.).
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional


def _escape_path(repo_path: str) -> str:
    """Convert an absolute path to Claude Code's escaped directory name.

    ``/Users/foo/bar`` → ``-Users-foo-bar``
    """
    return repo_path.replace("/", "-").replace("\\", "-")


def find_latest_log(repo_path: str) -> Optional[str]:
    """Find the most recent ``.jsonl`` conversation log for *repo_path*.

    Claude Code stores logs at::

        ~/.claude/projects/<escaped-path>/<session-id>.jsonl

    Returns the absolute path to the newest ``.jsonl`` by mtime, or ``None``.
    """
    escaped = _escape_path(repo_path)
    log_dir = Path.home() / ".claude" / "projects" / escaped
    if not log_dir.is_dir():
        return None

    jsonl_files: List[Path] = []
    try:
        for entry in log_dir.iterdir():
            if entry.suffix == ".jsonl" and entry.is_file():
                jsonl_files.append(entry)
    except OSError:
        return None

    if not jsonl_files:
        return None

    jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(jsonl_files[0])


def parse_conversation_log(jsonl_path: str) -> Dict:
    """Extract handoff-like context from a Claude Code conversation log.

    Parameters
    ----------
    jsonl_path:
        Absolute path to a ``.jsonl`` conversation log file.

    Returns
    -------
    dict with keys:
        task_summary          – first user message (first 300 chars)
        last_user_message     – last user prompt
        last_assistant_summary – last assistant text (first 500 chars)
        files_touched         – unique file paths from Read/Write/Edit tool_use
        key_commands          – Bash commands from tool_use inputs
        message_count         – total messages parsed
        started_at            – first message timestamp
        ended_at              – last message timestamp
        source                – ``"conversation_log_fallback"``
    """
    first_user: Optional[str] = None
    last_user: Optional[str] = None
    last_assistant_text: Optional[str] = None

    files_touched: List[str] = []
    key_commands: List[str] = []

    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    message_count = 0

    try:
        with open(jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("timestamp")
                msg = entry.get("message", {})
                role = msg.get("role") or entry.get("type", "")
                content = msg.get("content", "")
                message_count += 1

                if first_ts is None and ts:
                    first_ts = ts
                if ts:
                    last_ts = ts

                # --- user messages ---
                if role == "user":
                    text = _extract_text(content)
                    if text:
                        if first_user is None:
                            first_user = text
                        last_user = text

                # --- assistant messages ---
                elif role == "assistant":
                    text = _extract_text(content)
                    if text:
                        last_assistant_text = text

                    # extract tool_use blocks for files & commands
                    _extract_tool_artifacts(content, files_touched, key_commands)

    except (OSError, IOError):
        pass

    # deduplicate while preserving order
    seen_files: set = set()
    unique_files: List[str] = []
    for f in files_touched:
        if f not in seen_files:
            seen_files.add(f)
            unique_files.append(f)

    seen_cmds: set = set()
    unique_cmds: List[str] = []
    for c in key_commands:
        if c not in seen_cmds:
            seen_cmds.add(c)
            unique_cmds.append(c)

    return {
        "task_summary": (first_user or "")[:300],
        "last_user_message": last_user or "",
        "last_assistant_summary": (last_assistant_text or "")[:500],
        "files_touched": unique_files,
        "key_commands": unique_cmds,
        "message_count": message_count,
        "started_at": first_ts,
        "ended_at": last_ts,
        "source": "conversation_log_fallback",
    }


def _extract_text(content) -> Optional[str]:
    """Pull plain text from a message ``content`` field.

    ``content`` may be a plain string **or** a list of typed blocks::

        [{"type": "text", "text": "..."}, {"type": "tool_use", ...}]
    """
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "").strip()
                if t:
                    parts.append(t)
        return "\n".join(parts) if parts else None
    return None


def _extract_tool_artifacts(
    content, files_out: List[str], cmds_out: List[str]
) -> None:
    """Scan ``content`` blocks for tool_use and collect file paths / commands."""
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        inp = block.get("input", {})
        if not isinstance(inp, dict):
            continue

        # file-touching tools
        if name in ("Read", "Write", "Edit", "Glob"):
            fp = inp.get("file_path") or inp.get("path") or ""
            if fp:
                files_out.append(fp)
        # Bash commands
        elif name == "Bash":
            cmd = inp.get("command", "")
            if cmd:
                cmds_out.append(cmd)
