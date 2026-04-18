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
import time
from dataclasses import dataclass
from pathlib import Path

from dhee.router.ptr_store import _session_dir

_LEDGER_FILE = "edits.jsonl"
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()[:10]


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


def summarise(session_dir: Path | None = None) -> list[EditSummary]:
    """Read the ledger and collapse duplicate (path, hash) tuples."""
    sdir = session_dir or _session_dir()
    log = sdir / _LEDGER_FILE
    if not log.exists():
        return []

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
                key = (rec.get("p", ""), rec.get("h", ""))
                slot = by_key.setdefault(
                    key,
                    {"tool": rec.get("t", ""), "count": 0, "n": rec.get("n", 0), "at": 0.0},
                )
                slot["count"] += 1
                slot["at"] = max(slot["at"], rec.get("at", 0.0))
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
