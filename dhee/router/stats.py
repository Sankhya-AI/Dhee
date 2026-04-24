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
from typing import Any, Iterable

from dhee.router import ptr_store

CHARS_PER_TOKEN = 3.5


@dataclass
class RouterStats:
    sessions: int = 0
    total_calls: int = 0
    agent_id: str | None = None
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
            "agent_id": self.agent_id,
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


def _normalize_agent_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    aliases = {
        "claude_code": "claude-code",
        "claude code": "claude-code",
        "claude": "claude-code",
        "codex_cli": "codex",
    }
    return aliases.get(text, text.replace("_", "-").replace(" ", "-"))


def _agent_from_meta(meta: dict[str, Any]) -> str:
    explicit = meta.get("agent_id") or meta.get("harness")
    if explicit:
        return _normalize_agent_id(explicit)
    tool = str(meta.get("tool") or "").strip()
    if tool.startswith("Codex"):
        return "codex"
    if tool in {"Read", "Bash", "Agent", "Grep", "Glob"}:
        return "claude-code"
    return "unknown"


def _iter_meta_records() -> Iterable[tuple[str, dict[str, Any], Path]]:
    for sdir in _iter_session_dirs():
        for meta_file in sdir.glob("*.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(meta, dict):
                yield sdir.name, meta, meta_file


def _stored_chars(meta: dict[str, Any], meta_file: Path) -> int:
    chars = meta.get("char_count") or 0
    if chars:
        return int(chars)
    stdout_bytes = int(meta.get("stdout_bytes", 0) or 0)
    stderr_bytes = int(meta.get("stderr_bytes", 0) or 0)
    if stdout_bytes or stderr_bytes:
        return stdout_bytes + stderr_bytes
    ptr = str(meta.get("ptr") or "").strip()
    if not ptr:
        return 0
    raw_file = meta_file.with_suffix(".txt")
    if not raw_file.exists():
        return 0
    try:
        return len(raw_file.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def list_agent_stats() -> list[dict[str, Any]]:
    per_agent: dict[str, RouterStats] = {}
    session_hits: dict[str, set[str]] = {}
    for session_id, meta, meta_file in _iter_meta_records():
        agent = _agent_from_meta(meta)
        stats = per_agent.setdefault(agent, RouterStats(agent_id=agent))
        session_hits.setdefault(agent, set()).add(session_id)
        tool = str(meta.get("tool", "unknown"))
        stats.calls_by_tool[tool] = stats.calls_by_tool.get(tool, 0) + 1
        stats.total_calls += 1

        cls = meta.get("class")
        if tool == "Bash" and isinstance(cls, str):
            stats.bash_class_breakdown[cls] = stats.bash_class_breakdown.get(cls, 0) + 1

        stats.bytes_stored += _stored_chars(meta, meta_file)

    for rec in ptr_store.iter_expansion_records():
        agent = _normalize_agent_id(rec.get("agent_id"))
        per_agent.setdefault(agent, RouterStats(agent_id=agent)).expansion_calls += 1

    out: list[dict[str, Any]] = []
    for agent, stats in per_agent.items():
        stats.sessions = len(session_hits.get(agent, set()))
        stats.est_tokens_diverted = int(stats.bytes_stored / CHARS_PER_TOKEN)
        if stats.total_calls:
            stats.expansion_rate = stats.expansion_calls / stats.total_calls
        if stats.total_calls == 0 and stats.bytes_stored == 0:
            continue
        out.append(
            {
                "id": agent,
                "label": agent,
                "calls": stats.total_calls,
                "tokensSaved": stats.est_tokens_diverted,
                "bytesStored": stats.bytes_stored,
                "expansionRate": round(stats.expansion_rate, 4),
                "sessions": stats.sessions,
            }
        )
    return sorted(out, key=lambda item: (-int(item["tokensSaved"]), str(item["id"])))


def compute_stats(agent_id: str | None = None) -> RouterStats:
    """Aggregate ptr-store metadata across all sessions."""
    stats = RouterStats()
    tool_counts: Counter[str] = Counter()
    bash_classes: Counter[str] = Counter()
    session_hits: set[str] = set()
    selected_agent = None if agent_id in (None, "", "all") else _normalize_agent_id(agent_id)
    stats.agent_id = selected_agent

    for session_id, meta, meta_file in _iter_meta_records():
        meta_agent = _agent_from_meta(meta)
        if selected_agent and meta_agent != selected_agent:
            continue
        session_hits.add(session_id)
        tool = meta.get("tool", "unknown")
        tool_counts[tool] += 1
        stats.total_calls += 1

        cls = meta.get("class")
        if tool == "Bash" and isinstance(cls, str):
            bash_classes[cls] += 1

        stats.bytes_stored += _stored_chars(meta, meta_file)

    # Expansion counts from the per-session audit logs.
    expansions = 0
    for rec in ptr_store.iter_expansion_records():
        rec_agent = _normalize_agent_id(rec.get("agent_id"))
        if selected_agent and rec_agent != selected_agent:
            continue
        expansions += 1
    stats.sessions = len(session_hits) if selected_agent else len(_iter_session_dirs())
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
        sessions = _iter_session_dirs()
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
