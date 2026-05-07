"""Per-session edit ledger — Phase 7 Write/Edit compaction.

Each successful Edit / Write / MultiEdit on PostToolUse appends a compact
record. On PreCompact we render a deduped summary so the post-compaction
model keeps a faithful list of what changed without re-reading every
verbose Edit tool_result that the host's own compactor will try to
reconstruct into prose.

Dedup rule: identical (path, new_hash) tuples collapse into one entry
with an occurrence count. Repeated re-edits of the same snippet — common
when the model iterates on a function — cost one line, not N.

Filesystem-only, per MCP-process session dir (shared with ptr_store).
Best-effort: every operation swallows exceptions. Never fails the hook.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dhee.router.ptr_store import _session_dir

_LEDGER_FILE = "edits.jsonl"
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# Default freshness window: anything older than this is considered prior-session
# scratchwork and never surfaces in an injection.
_DEFAULT_MAX_AGE_SECONDS = 6 * 3600

# Path prefixes that are throwaway scratchwork — never inject these.
_PURGE_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/")


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()[:10]


def _current_session_id() -> str:
    return os.environ.get("DHEE_SESSION_ID") or ""


def _current_cwd() -> str:
    try:
        return os.getcwd()
    except OSError:
        return ""


def record(tool: str, path: str, new_content: str) -> None:
    """Append one edit record. Silent on failure."""
    if tool not in _WRITE_TOOLS or not path:
        return
    try:
        rec = {
            "t": tool,
            "p": path,
            "h": _hash(new_content or ""),
            "n": len(new_content or ""),
            "at": time.time(),
            "s": _current_session_id(),
            "cwd": _current_cwd(),
        }
        log = _session_dir() / _LEDGER_FILE
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        return


@dataclass
class EditSummary:
    path: str
    tool: str
    occurrences: int
    total_bytes: int
    last_at: float

    @property
    def deduped(self) -> int:
        return max(0, self.occurrences - 1)


def summarise(
    session_dir: Path | None = None,
    *,
    session_id: Optional[str] = None,
    repo: Optional[str] = None,
    max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS,
) -> list[EditSummary]:
    """Read the ledger and collapse duplicate (path, hash) tuples.

    Filters (all best-effort, defaulting to the current environment):

    - ``session_id`` — when set (or implicit via ``DHEE_SESSION_ID`` env),
      drop rows whose recorded session does not match. Rows with no session
      field pass through for backward compat.
    - ``repo`` — keep only paths that sit under this directory (defaults to
      the current cwd). Rows with no cwd field pass through for backward
      compat.
    - ``max_age_seconds`` — drop rows older than this window (default 6h).
    - ``/tmp/`` and ``/var/folders/`` paths are dropped unconditionally —
      these are throwaway scratchwork that should never appear in an
      injection.
    """
    sdir = session_dir or _session_dir()
    log = sdir / _LEDGER_FILE
    if not log.exists():
        return []

    active_session = session_id if session_id is not None else _current_session_id()
    active_repo = repo if repo is not None else _current_cwd()
    min_at = time.time() - max(0.0, float(max_age_seconds))
    active_repo_norm = active_repo.rstrip("/") + "/" if active_repo else ""

    # key = (path, hash). We also need per-key tool + counts.
    by_key: dict[tuple[str, str], dict] = {}
    try:
        with log.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                path = str(rec.get("p") or "")
                if not path or any(path.startswith(p) for p in _PURGE_PREFIXES):
                    continue
                at = float(rec.get("at") or 0.0)
                if at and at < min_at:
                    continue

                rec_session = rec.get("s")
                if active_session and rec_session and rec_session != active_session:
                    continue

                rec_cwd = str(rec.get("cwd") or "")
                if active_repo_norm and rec_cwd:
                    rec_cwd_norm = rec_cwd.rstrip("/") + "/"
                    # keep entries whose recorded cwd overlaps the active repo
                    # (either direction — handles monorepo subdirs both ways).
                    if not (
                        rec_cwd_norm.startswith(active_repo_norm)
                        or active_repo_norm.startswith(rec_cwd_norm)
                    ):
                        continue

                key = (path, rec.get("h", ""))
                slot = by_key.setdefault(
                    key,
                    {"tool": rec.get("t", ""), "count": 0, "n": rec.get("n", 0), "at": 0.0},
                )
                slot["count"] += 1
                slot["at"] = max(slot["at"], at)
    except Exception:
        return []

    # Collapse further by path — one line per file, summing occurrences
    # across distinct hashes so the block stays short in long sessions.
    by_path: dict[str, EditSummary] = {}
    for (path, _h), slot in by_key.items():
        s = by_path.get(path)
        if s is None:
            by_path[path] = EditSummary(
                path=path,
                tool=slot["tool"],
                occurrences=slot["count"],
                total_bytes=slot["n"],
                last_at=slot["at"],
            )
        else:
            s.occurrences += slot["count"]
            s.total_bytes += slot["n"]
            s.last_at = max(s.last_at, slot["at"])

    return sorted(by_path.values(), key=lambda s: s.last_at, reverse=True)


def render_block(max_files: int = 20) -> str:
    """Produce a compact injection block. Empty string when no edits."""
    entries = summarise()
    if not entries:
        return ""
    lines = ["<edits desc=\"this session's file changes\">"]
    for e in entries[:max_files]:
        dup = f" x{e.occurrences}" if e.occurrences > 1 else ""
        lines.append(f"  {e.path}{dup}")
    overflow = len(entries) - max_files
    if overflow > 0:
        lines.append(f"  ... +{overflow} more files")
    lines.append("</edits>")
    return "\n".join(lines)
