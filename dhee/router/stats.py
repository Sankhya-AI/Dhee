"""Router observability.

Aggregates ptr-store metadata into a usage report. Honest about what it
measures and what it can't:

- *Can* measure: how many times the router was called, how many bytes of
  raw content were kept out of the context, the command-class
  distribution, expansion-rate (callers retrieving raw after digest).
- *Can't* measure: whether the model would have otherwise called native
  Read/Bash. For that, compare to a baseline session token burn.

The numbers here are "bytes diverted behind ptrs", not "tokens saved in
the conversation". Tokens-saved is an estimate (bytes / 3.5).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dhee.router import ptr_store

CHARS_PER_TOKEN = 3.5


@dataclass
class RouterStats:
    sessions: int = 0
    total_calls: int = 0
    calls_by_tool: dict[str, int] = field(default_factory=dict)
    bash_class_breakdown: dict[str, int] = field(default_factory=dict)
    bytes_stored: int = 0
    est_tokens_diverted: int = 0
    expansion_calls: int = 0
    expansion_rate: float = 0.0
    edit_files: int = 0
    edit_events: int = 0
    edit_deduped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.sessions,
            "total_calls": self.total_calls,
            "calls_by_tool": dict(self.calls_by_tool),
            "bash_class_breakdown": dict(self.bash_class_breakdown),
            "bytes_stored": self.bytes_stored,
            "est_tokens_diverted": self.est_tokens_diverted,
            "expansion_calls": self.expansion_calls,
            "expansion_rate": round(self.expansion_rate, 4),
            "edit_files": self.edit_files,
            "edit_events": self.edit_events,
            "edit_deduped": self.edit_deduped,
        }


def _iter_session_dirs() -> list[Path]:
    root = ptr_store._root()
    if not root.exists():
        return []
    return [d for d in root.iterdir() if d.is_dir()]


def compute_stats() -> RouterStats:
    """Aggregate ptr-store metadata across all sessions."""
    stats = RouterStats()
    tool_counts: Counter[str] = Counter()
    bash_classes: Counter[str] = Counter()
    expansions = 0

    sessions = _iter_session_dirs()
    stats.sessions = len(sessions)

    for sdir in sessions:
        for meta_file in sdir.glob("*.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            tool = meta.get("tool", "unknown")
            tool_counts[tool] += 1
            stats.total_calls += 1

            cls = meta.get("class")
            if tool == "Bash" and isinstance(cls, str):
                bash_classes[cls] += 1

            chars = meta.get("char_count") or 0
            if not chars:
                chars = int(meta.get("stdout_bytes", 0) or 0) + int(
                    meta.get("stderr_bytes", 0) or 0
                )
            stats.bytes_stored += int(chars)

    # Expansion counts from the per-session audit logs.
    expansions = sum(ptr_store.iter_expansion_counts().values())
    stats.expansion_calls = expansions
    stats.calls_by_tool = dict(tool_counts)
    stats.bash_class_breakdown = dict(bash_classes)
    stats.est_tokens_diverted = int(stats.bytes_stored / CHARS_PER_TOKEN)
    if stats.total_calls:
        stats.expansion_rate = expansions / stats.total_calls

    # Phase 7: aggregate edit ledger across sessions.
    try:
        from dhee.router.edit_ledger import summarise as _edit_summ

        paths: set[str] = set()
        total_events = 0
        for sdir in sessions:
            for e in _edit_summ(sdir):
                paths.add(e.path)
                total_events += e.occurrences
        stats.edit_files = len(paths)
        stats.edit_events = total_events
        stats.edit_deduped = max(0, total_events - stats.edit_files)
    except Exception:
        pass

    return stats


def format_human(stats: RouterStats) -> str:
    """Pretty-print stats for the terminal."""
    if stats.total_calls == 0:
        return (
            "  No router activity recorded.\n"
            "  Enable with: dhee router enable\n"
            "  Then restart Claude Code and use the agent normally."
        )

    lines = [
        f"  Sessions observed:   {stats.sessions}",
        f"  Router calls:        {stats.total_calls}",
        f"  Bytes diverted:      {stats.bytes_stored:,} "
        f"(~{stats.est_tokens_diverted:,} tokens)",
    ]
    if stats.calls_by_tool:
        lines.append("  By tool:")
        for tool, n in sorted(
            stats.calls_by_tool.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"    {tool:<8} {n}")
    if stats.bash_class_breakdown:
        lines.append("  Bash classes:")
        for cls, n in sorted(
            stats.bash_class_breakdown.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"    {cls:<12} {n}")
    lines.append(
        f"  Expansions:          {stats.expansion_calls} "
        f"({stats.expansion_rate:.1%})"
    )
    if stats.total_calls:
        if stats.expansion_rate < 0.05:
            lines.append("    (model rarely expands — digests appear sufficient)")
        elif stats.expansion_rate > 0.30:
            lines.append("    (high expand rate — digests may be too shallow)")
    if stats.edit_events:
        lines.append(
            f"  Edits logged:        {stats.edit_events} across "
            f"{stats.edit_files} files (dedup: {stats.edit_deduped})"
        )
    return "\n".join(lines)
