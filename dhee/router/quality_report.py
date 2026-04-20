"""Session-quality report — the honest evaluation artifact.

Instead of a live A/B, we measure what the router + hooks actually did
across the user's *real* recorded sessions and the *current* ptr-store.
One JSON file, re-runnable, diffable over time.

What's in the report:

    router.stats        — ptr-store aggregates (calls, bytes diverted,
                          expansion rate, bash classes, edit ledger)
    router.replay       — counterfactual projection across every JSONL
                          session in ``~/.claude/projects/<slug>/``:
                          raw vs digest token totals, per-tool calls.
    edits               — deduped per-file edit list for current session.
    hooks               — which Dhee hook events are installed.
    settings            — router enable state, enforcement flag.
    env                 — dhee version, timestamp, session id.

Quality signals (interpreted in human output):
    expansion_rate < 5%  → digests sufficient (no quality regression)
    expansion_rate > 30% → digests too shallow (quality pressure)
    replay saved_pct     → projected token savings vs native flow

Non-goals: does not run the model. That's Phase 9-full. This is the
telemetry layer — a fair baseline the user can cite.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class QualityReport:
    dhee_version: str = ""
    generated_at: float = 0.0
    router: dict[str, Any] = field(default_factory=dict)
    critical_surface: dict[str, Any] = field(default_factory=dict)
    replay: dict[str, Any] = field(default_factory=dict)
    edits: dict[str, Any] = field(default_factory=dict)
    hooks: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _router_section() -> dict[str, Any]:
    try:
        from dhee.router.stats import compute_stats

        return compute_stats().to_dict()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _critical_surface_section() -> dict[str, Any]:
    try:
        import os

        from dhee.configs.base import _dhee_data_dir
        from dhee.db.sqlite import SQLiteManager

        db = SQLiteManager(os.path.join(_dhee_data_dir(), "history.db"))
        return db.summarize_route_decisions(user_id="default")
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _replay_section(sessions_dir: Path | None = None, limit: int = 0) -> dict[str, Any]:
    """Run the replay harness in-process and collect aggregate numbers."""
    try:
        from dhee.benchmarks.router_replay import (
            _default_sessions_dir,
            replay_session,
        )

        sdir = sessions_dir or _default_sessions_dir()
        if not sdir.exists():
            return {"error": f"sessions dir missing: {sdir}"}

        transcripts = sorted(
            sdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if limit:
            transcripts = transcripts[:limit]

        raw = digest = calls = 0
        by_tool: dict[str, int] = {}
        warnings = 0
        turns = 0
        cache_read = 0
        cache_creation = 0
        tool_result_tokens = 0
        for p in transcripts:
            r = replay_session(p)
            raw += r.raw_tokens
            digest += r.digest_tokens
            calls += r.total_calls
            for t, n in r.calls_by_tool.items():
                by_tool[t] = by_tool.get(t, 0) + n
            warnings += len(r.warnings)
            turns += r.assistant_turns
            cache_read += r.cache_read_input_tokens
            cache_creation += r.cache_creation_input_tokens
            tool_result_tokens += r.tool_result_tokens

        net_saved = raw - digest
        saved_pct = round(net_saved / raw * 100, 2) if raw else 0.0
        cache_read_per_turn = int(cache_read / turns) if turns else 0
        projected_cache_read_per_turn = (
            int((cache_read - net_saved) / turns) if turns and cache_read else 0
        )
        tool_result_share = round(tool_result_tokens / cache_read, 3) if cache_read else 0.0

        # Promise #1 gate: target < 30K avg cache-read tokens per turn.
        cache_read_target = 30_000
        promise1_met = projected_cache_read_per_turn < cache_read_target if turns else None

        return {
            "sessions_dir": str(sdir),
            "sessions": len(transcripts),
            "assistant_turns": turns,
            "total_calls": calls,
            "calls_by_tool": by_tool,
            "raw_tokens": raw,
            "digest_tokens": digest,
            "net_saved_tokens": net_saved,
            "saved_pct": saved_pct,
            "cache_read_tokens_total": cache_read,
            "cache_creation_tokens_total": cache_creation,
            "cache_read_per_turn": cache_read_per_turn,
            "projected_cache_read_per_turn": projected_cache_read_per_turn,
            "cache_read_target_per_turn": cache_read_target,
            "promise1_met": promise1_met,
            "tool_result_tokens": tool_result_tokens,
            "tool_result_share": tool_result_share,
            "warnings_count": warnings,
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _edits_section() -> dict[str, Any]:
    try:
        from dhee.router.edit_ledger import summarise
        from dhee.router.ptr_store import _root

        # Aggregate across every session dir — hooks run in separate
        # processes so edits land in many session_dirs.
        by_path: dict[str, int] = {}
        root = _root()
        if root.exists():
            for sdir in root.iterdir():
                if not sdir.is_dir():
                    continue
                for e in summarise(sdir):
                    by_path[e.path] = by_path.get(e.path, 0) + e.occurrences

        top = sorted(by_path.items(), key=lambda kv: -kv[1])[:10]
        return {
            "files": len(by_path),
            "total_events": sum(by_path.values()),
            "deduped": sum(max(0, v - 1) for v in by_path.values()),
            "top": [{"path": p, "occurrences": n} for p, n in top],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _hooks_section() -> dict[str, Any]:
    try:
        settings_path = Path.home() / ".claude" / "settings.json"
        if not settings_path.exists():
            return {"installed_events": [], "settings_path": str(settings_path)}
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {}) or {}
        installed = []
        for event, entries in hooks.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                for h in entry.get("hooks", []):
                    if "dhee.hooks.claude_code" in h.get("command", ""):
                        installed.append(event)
                        break
        return {"installed_events": sorted(set(installed))}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _settings_section() -> dict[str, Any]:
    try:
        from dhee.router.install import status
        from dhee.router.pre_tool_gate import _flag_file

        s = status()
        return {
            "router_enabled": s.enabled,
            "allowed_tools": s.allowed_tools,
            "env_flag": s.env_flag,
            "enforce_on": _flag_file().exists(),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def build_report(sessions_dir: Path | None = None, limit: int = 0) -> QualityReport:
    try:
        from dhee import __version__
    except Exception:
        __version__ = "unknown"

    return QualityReport(
        dhee_version=__version__,
        generated_at=time.time(),
        router=_router_section(),
        critical_surface=_critical_surface_section(),
        replay=_replay_section(sessions_dir=sessions_dir, limit=limit),
        edits=_edits_section(),
        hooks=_hooks_section(),
        settings=_settings_section(),
    )


def save_report(report: QualityReport, path: Path | None = None) -> Path:
    out = path or (Path.home() / ".dhee" / "session_quality_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    return out


def format_human(report: QualityReport) -> str:
    r = report.router
    cs = report.critical_surface
    rep = report.replay
    e = report.edits
    s = report.settings
    h = report.hooks
    lines = [
        f"dhee {report.dhee_version}  |  {time.strftime('%Y-%m-%d %H:%M', time.localtime(report.generated_at))}",
        "",
        "[ settings ]",
        f"  router enabled: {s.get('router_enabled')}   enforce: {s.get('enforce_on')}",
        f"  hooks:          {', '.join(h.get('installed_events', [])) or '(none)'}",
        "",
        "[ router ptr-store ]",
        f"  sessions:       {r.get('sessions', 0)}",
        f"  calls:          {r.get('total_calls', 0)}   by tool: {r.get('calls_by_tool', {})}",
        f"  bytes diverted: {r.get('bytes_stored', 0):,}  (~{r.get('est_tokens_diverted', 0):,} tokens)",
        f"  expansions:     {r.get('expansion_calls', 0)}  ({r.get('expansion_rate', 0):.1%})",
    ]
    er = r.get("expansion_rate", 0) or 0
    if r.get("total_calls", 0):
        if er < 0.05:
            lines.append("    → quality signal: digests sufficient (rare expansion)")
        elif er > 0.30:
            lines.append("    → quality signal: digests too shallow (raise depth)")
        else:
            lines.append("    → quality signal: healthy")
    if cs and not cs.get("error") and cs.get("total_decisions", 0):
        lines += [
            "",
            "[ critical surface ]",
            f"  decisions:      {cs.get('total_decisions', 0)}",
            f"  by route:       {cs.get('by_route', {})}",
            f"  by packet:      {cs.get('by_packet_kind', {})}",
            f"  saved tokens:   {cs.get('total_token_delta', 0):,}"
            f"  (reads: {cs.get('read_saved_tokens', 0):,}, artifact reuse: {cs.get('artifact_reuse_saved_tokens', 0):,})",
            f"  avg fits:       semantic={cs.get('avg_semantic_fit', 0):.2f}"
            f" structural={cs.get('avg_structural_fit', 0):.2f}"
            f" confidence={cs.get('avg_confidence', 0):.2f}",
        ]
    lines += [
        "",
        "[ replay projection (counterfactual) ]",
        f"  sessions:       {rep.get('sessions', 0)}",
        f"  assistant turns: {rep.get('assistant_turns', 0)}",
        f"  tool calls:     {rep.get('total_calls', 0)}   by tool: {rep.get('calls_by_tool', {})}",
        f"  raw tokens:     {rep.get('raw_tokens', 0):,}",
        f"  digest tokens:  {rep.get('digest_tokens', 0):,}",
        f"  net saved:      {rep.get('net_saved_tokens', 0):,}  ({rep.get('saved_pct', 0):.1f}%)",
        "",
        "[ promise 1 — token savings (target < 30K cache-read / turn) ]",
        f"  cache-read / turn today:     {rep.get('cache_read_per_turn', 0):,}",
        f"  projected with router:       {rep.get('projected_cache_read_per_turn', 0):,}",
        f"  tool_result share of cache:  {rep.get('tool_result_share', 0):.1%}",
    ]
    pm = rep.get("promise1_met")
    if pm is True:
        lines.append("    → promise met: projected cache-read per turn below target")
    elif pm is False:
        lines.append("    → promise not yet met: projected cache-read per turn above target")
    lines += [
        "",
        "[ edits (current session) ]",
        f"  files edited:   {e.get('files', 0)}",
        f"  events:         {e.get('total_events', 0)}   deduped: {e.get('deduped', 0)}",
    ]
    return "\n".join(lines)


def format_share(report: QualityReport) -> str:
    """Customer-shareable Markdown. No paths, no session ids, no env.

    Shows what the router did on *this* user's real sessions and how to
    reproduce / roll back. Intended output: paste into an email, a PR
    description, or a Slack thread.
    """
    r = report.router or {}
    cs = report.critical_surface or {}
    rep = report.replay or {}
    s = report.settings or {}
    h = report.hooks or {}

    calls = int(rep.get("total_calls", 0) or 0)
    raw = int(rep.get("raw_tokens", 0) or 0)
    digest = int(rep.get("digest_tokens", 0) or 0)
    saved = int(rep.get("net_saved_tokens", 0) or 0)
    saved_pct = float(rep.get("saved_pct", 0) or 0)
    expansion = float(r.get("expansion_rate", 0) or 0)

    hooks = ", ".join(h.get("installed_events", [])) or "(none)"
    enforce = "on" if s.get("enforce_on") else "off"
    enabled = "yes" if s.get("router_enabled") else "no"

    lines = [
        f"# Dhee router — token savings report",
        "",
        f"- dhee version: `{report.dhee_version}`",
        f"- router enabled: **{enabled}**, enforce: **{enforce}**",
        f"- hooks installed: {hooks}",
        "",
        "## Projected savings (counterfactual replay of real sessions)",
        "",
        f"- sessions replayed: **{rep.get('sessions', 0)}**",
        f"- assistant turns: **{rep.get('assistant_turns', 0)}**",
        f"- tool calls replayed: **{calls:,}**",
        f"- raw tokens (native flow): **{raw:,}**",
        f"- digest tokens (router flow): **{digest:,}**",
        f"- net saved: **{saved:,}**  (**{saved_pct:.1f}%**)",
        "",
        "## Cache-read per turn (promise #1: target < 30K)",
        "",
        f"- today (native): **{rep.get('cache_read_per_turn', 0):,}**",
        f"- projected (router): **{rep.get('projected_cache_read_per_turn', 0):,}**",
        f"- tool_result share of cache-read: **{rep.get('tool_result_share', 0):.1%}**",
        "",
        "## Quality signal",
        "",
        f"- expansion rate: **{expansion:.1%}**  "
        "(how often the model asked for full raw content behind a digest)",
    ]
    if cs and not cs.get("error") and cs.get("total_decisions", 0):
        lines += [
            f"- critical-surface decisions: **{cs.get('total_decisions', 0)}**",
            f"- saved tokens from routed reads + artifact reuse: **{cs.get('total_token_delta', 0):,}**",
            f"- route mix: `{cs.get('by_route', {})}`",
        ]
    if calls > 0:
        if expansion < 0.05:
            lines.append("- digests are sufficient — no quality regression observed")
        elif expansion > 0.30:
            lines.append("- digests are too shallow — raise depth if accuracy drops")
        else:
            lines.append("- healthy expansion rate")
    lines += [
        "",
        "## How to reproduce",
        "",
        "```bash",
        "pip install -U dhee",
        "dhee router enable      # installs hooks, adds permissions, turns enforce on",
        "# … use Claude Code normally for a few sessions …",
        "dhee router report      # regenerates this report",
        "```",
        "",
        "## Rollback",
        "",
        "One command, no residue:",
        "",
        "```bash",
        "dhee router disable",
        "```",
        "",
        "Removes the router tools from `permissions.allow`, clears the "
        "`DHEE_ROUTER` env flag on the Dhee MCP server, and deletes the "
        "enforce flag file. A backup of `~/.claude/settings.json` is "
        "written to `settings.json.dhee-router-backup` before either "
        "enable or disable.",
    ]
    return "\n".join(lines)
