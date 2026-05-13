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
    context_governance: dict[str, Any] = field(default_factory=dict)
    tool_schema: dict[str, Any] = field(default_factory=dict)
    replay: dict[str, Any] = field(default_factory=dict)
    quality_gates: dict[str, Any] = field(default_factory=dict)
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


def _context_governance_section() -> dict[str, Any]:
    try:
        import os

        from dhee.context_state import ContextStateStore

        repo = os.environ.get("DHEE_REPO") or os.getcwd()
        store = ContextStateStore(
            repo=repo,
            workspace_id=repo,
            user_id=os.environ.get("DHEE_USER_ID", "default"),
            agent_id=os.environ.get("DHEE_AGENT_ID", "quality_report"),
        )
        debt = store.debt_summary(top=False)
        state = store.load()
        return {
            "task_epoch": int(state.get("task_epoch") or 1),
            "state_revision": int(state.get("state_revision") or 0),
            "cache_tier_breakdown": debt.get("cache_tier_breakdown", {}),
            "receipt_count": debt.get("receipt_count", 0),
            "assertion_mismatch_count": debt.get("assertion_mismatch_count", 0),
            "reread_short_circuit_count": debt.get("reread_short_circuit_count", 0),
            "suppression_equivalence_projection": debt.get("suppression_equivalence_projection", {}),
            "disclaimer": "Replay estimates savings and pressure, not live behavioral equivalence.",
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _tool_schema_section() -> dict[str, Any]:
    try:
        from dhee.mcp_slim import tool_schema_report

        return tool_schema_report()
    except Exception as exc:
        try:
            source = (Path(__file__).resolve().parents[1] / "mcp_slim.py").read_text(encoding="utf-8")
            start = source.index("TOOLS = [")
            end = source.index("# ---------------------------------------------------------------------------\n# Tool-schema footprint", start)
            tool_block = source[start:end]
            original_tokens = max(0, int(len(tool_block) / 3.5))
            tool_count = tool_block.count("Tool(")
            tiers = {}
            for tier, fraction in {"low": 0.62, "moderate": 0.42, "strong": 0.28, "max": 0.08}.items():
                tokens = int(original_tokens * fraction)
                tiers[tier] = {
                    "tokens": tokens,
                    "saved_tokens": max(0, original_tokens - tokens),
                    "saved_pct": round((original_tokens - tokens) / original_tokens * 100, 2) if original_tokens else 0.0,
                }
            return {
                "tool_count": tool_count,
                "original_tokens": original_tokens,
                "tiers": tiers,
                "policy": "Static fallback estimate; mcp package unavailable. Do not mutate tool definitions mid-session.",
                "fallback_reason": f"{type(exc).__name__}: {exc}",
            }
        except Exception:
            return {"error": f"{type(exc).__name__}: {exc}"}


def _replay_section(
    sessions_dir: Path | None = None,
    limit: int = 0,
    *,
    harness: str = "claude_code",
    golden_path: Path | None = None,
) -> dict[str, Any]:
    """Run the replay harness in-process and collect aggregate numbers."""
    try:
        from dhee.benchmarks.router_replay import (
            aggregate_reports,
            discover_transcripts,
            load_golden_annotations,
            replay_session,
        )

        transcripts = discover_transcripts(
            sessions_dir=sessions_dir,
            harness=harness,
            limit=limit,
        )
        if sessions_dir and not sessions_dir.exists():
            return {"error": f"sessions dir missing: {sessions_dir}"}
        annotations = load_golden_annotations(golden_path)
        replay_harness = "auto" if harness == "all" else harness
        reports = [
            replay_session(p, harness=replay_harness, annotations=annotations)
            for p in transcripts
        ]
        aggregate = aggregate_reports(reports)

        # Promise #1 gate: target < 30K avg cache-read tokens per turn.
        cache_read_target = 30_000
        promise1_met = (
            aggregate["projected_cache_read_per_turn"] < cache_read_target
            if aggregate["assistant_turns"]
            else None
        )

        return {
            "sessions_dir": str(sessions_dir) if sessions_dir else "",
            "harness": harness,
            "golden_path": str(golden_path) if golden_path else "",
            **aggregate,
            "cache_read_target_per_turn": cache_read_target,
            "promise1_met": promise1_met,
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _gate_status(value: Any, *, op: str, target: float, samples: int = 1) -> dict[str, Any]:
    try:
        actual = float(value)
    except (TypeError, ValueError):
        return {"passed": None, "actual": value, "target": target, "reason": "missing_value"}
    if samples <= 0:
        return {"passed": None, "actual": actual, "target": target, "reason": "insufficient_samples"}
    if op == ">=":
        passed = actual >= target
    elif op == "<=":
        passed = actual <= target
    else:
        passed = False
    return {"passed": passed, "actual": actual, "target": target}


def _quality_gates_section(
    *,
    router: dict[str, Any],
    replay: dict[str, Any],
    context_governance: dict[str, Any],
) -> dict[str, Any]:
    """Release-facing gates for the Developer Brain north-star metrics."""
    replay_calls = int(replay.get("total_calls", 0) or 0) if not replay.get("error") else 0
    router_calls = int(router.get("total_calls", 0) or 0) if not router.get("error") else 0
    receipt_count = int(context_governance.get("receipt_count", 0) or 0) if not context_governance.get("error") else 0
    parity = replay.get("task_parity") if isinstance(replay.get("task_parity"), dict) else {}
    parity_failures = int(parity.get("fail", 0) or 0)
    parity_avg_score = parity.get("avg_score")
    parity_score_count = int(parity.get("score_count", 0) or 0)
    annotated_sessions = int(replay.get("annotated_sessions", 0) or 0)
    pending_reviews = int(replay.get("pending_review_sessions", 0) or 0)

    gates = {
        "router_token_savings": {
            **_gate_status(replay.get("saved_pct"), op=">=", target=50.0, samples=replay_calls),
            "unit": "percent",
            "source": "router replay projection",
        },
        "expansion_rate": {
            **_gate_status((float(router.get("expansion_rate", 0) or 0) * 100.0), op="<=", target=15.0, samples=router_calls),
            "unit": "percent",
            "source": "ptr-store expansion telemetry",
        },
        "cache_read_per_turn": {
            **_gate_status(replay.get("projected_cache_read_per_turn"), op="<=", target=30_000.0, samples=int(replay.get("assistant_turns", 0) or 0)),
            "unit": "tokens",
            "source": "assistant usage + replay projection",
        },
        "context_governance": {
            **_gate_status(context_governance.get("assertion_mismatch_count", 0), op="<=", target=0.0, samples=max(1, receipt_count)),
            "unit": "incidents",
            "source": "compiled context admission receipts",
        },
        "stale_context_incidents": {
            **_gate_status(replay.get("stale_context_incidents", 0), op="<=", target=0.0, samples=int(replay.get("annotated_sessions", 0) or 0)),
            "unit": "incidents",
            "source": "golden replay annotations",
        },
        "task_parity_failures": {
            **_gate_status(parity_failures, op="<=", target=0.0, samples=annotated_sessions),
            "unit": "sessions",
            "source": "golden replay annotations",
        },
        "task_parity_pending_review": {
            **_gate_status(pending_reviews, op="<=", target=0.0, samples=annotated_sessions),
            "unit": "sessions",
            "source": "golden replay annotations",
        },
        "task_parity_score": {
            **_gate_status(parity_avg_score, op=">=", target=0.95, samples=parity_score_count),
            "unit": "score",
            "source": "golden replay annotations",
        },
    }
    statuses = [gate.get("passed") for gate in gates.values()]
    if any(status is False for status in statuses):
        verdict = "attention"
    elif statuses and all(status is True for status in statuses):
        verdict = "pass"
    else:
        verdict = "insufficient_data"
    return {
        "verdict": verdict,
        "targets": {
            "router_token_savings_pct": 50.0,
            "expansion_rate_pct_max": 15.0,
            "cache_read_per_turn_max": 30_000,
            "context_governance_incidents_max": 0,
            "stale_context_incidents_max": 0,
            "task_parity_failures_max": 0,
            "task_parity_pending_review_max": 0,
            "task_parity_score_min": 0.95,
        },
        "gates": gates,
        "note": "These gates are release-quality signals. None alone proves live task parity; replay and expansion data must be read together.",
    }


def gate_summary(report: QualityReport, *, allow_insufficient: bool = False) -> dict[str, Any]:
    gates = (report.quality_gates or {}).get("gates") or {}
    failed = sorted(name for name, gate in gates.items() if gate.get("passed") is False)
    pending = sorted(name for name, gate in gates.items() if gate.get("passed") is None)
    verdict = (report.quality_gates or {}).get("verdict", "unknown")
    ok = not failed and (allow_insufficient or not pending)
    return {
        "ok": ok,
        "verdict": verdict if ok or not allow_insufficient else ("pass_with_insufficient_data" if not failed else verdict),
        "failed_gates": failed,
        "pending_gates": pending,
        "allow_insufficient": bool(allow_insufficient),
    }


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


def build_report(
    sessions_dir: Path | None = None,
    limit: int = 0,
    *,
    harness: str = "claude_code",
    golden_path: Path | None = None,
) -> QualityReport:
    try:
        from dhee import __version__
    except Exception:
        __version__ = "unknown"

    router = _router_section()
    critical_surface = _critical_surface_section()
    context_governance = _context_governance_section()
    tool_schema = _tool_schema_section()
    replay = _replay_section(
        sessions_dir=sessions_dir,
        limit=limit,
        harness=harness,
        golden_path=golden_path,
    )
    return QualityReport(
        dhee_version=__version__,
        generated_at=time.time(),
        router=router,
        critical_surface=critical_surface,
        context_governance=context_governance,
        tool_schema=tool_schema,
        replay=replay,
        quality_gates=_quality_gates_section(
            router=router,
            replay=replay,
            context_governance=context_governance,
        ),
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
    cg = report.context_governance
    ts = report.tool_schema
    rep = report.replay
    qg = report.quality_gates
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
    if qg:
        lines += [
            "",
            "[ quality gates ]",
            f"  verdict:        {qg.get('verdict', 'unknown')}",
        ]
        for name, gate in (qg.get("gates") or {}).items():
            status = gate.get("passed")
            marker = "pass" if status is True else ("attention" if status is False else "pending")
            actual = gate.get("actual")
            target = gate.get("target")
            unit = gate.get("unit") or ""
            lines.append(f"  {name}: {marker}  actual={actual} target={target} {unit}".rstrip())
    if cg and not cg.get("error"):
        lines += [
            "",
            "[ context governance ]",
            f"  receipts:       {cg.get('receipt_count', 0)}",
            f"  cache tiers:    {cg.get('cache_tier_breakdown', {})}",
            f"  assertion gaps: {cg.get('assertion_mismatch_count', 0)}",
            f"  reread stops:   {cg.get('reread_short_circuit_count', 0)}",
        ]
        projection = cg.get("suppression_equivalence_projection", {}) or {}
        lines.append(f"  suppression proxy risk: {projection.get('risk', 'unknown')}")
    if ts and not ts.get("error"):
        tiers = ts.get("tiers", {}) or {}
        strong = tiers.get("strong", {}) if isinstance(tiers, dict) else {}
        lines += [
            "",
            "[ tool schema footprint ]",
            f"  tools:          {ts.get('tool_count', 0)}",
            f"  original:       {ts.get('original_tokens', 0):,} tokens",
            f"  strong tier:    {strong.get('tokens', 0):,} tokens ({strong.get('saved_pct', 0):.1f}% projected savings)",
        ]
    lines += [
        "",
        "[ replay projection (counterfactual) ]",
        f"  harness:        {rep.get('harness', 'claude_code')}",
        f"  sessions:       {rep.get('sessions', 0)}   by harness: {rep.get('sessions_by_harness', {})}",
        f"  assistant turns: {rep.get('assistant_turns', 0)}",
        f"  tool calls:     {rep.get('total_calls', 0)}   by tool: {rep.get('calls_by_tool', {})}",
        f"  raw tokens:     {rep.get('raw_tokens', 0):,}",
        f"  digest tokens:  {rep.get('digest_tokens', 0):,}",
        f"  net saved:      {rep.get('net_saved_tokens', 0):,}  ({rep.get('saved_pct', 0):.1f}%)",
        f"  golden:         annotated={rep.get('annotated_sessions', 0)} pending={rep.get('pending_review_sessions', 0)} stale={rep.get('stale_context_incidents', 0)} parity={rep.get('task_parity', {})}",
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
    cg = report.context_governance or {}
    ts = report.tool_schema or {}
    rep = report.replay or {}
    qg = report.quality_gates or {}
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
        f"- quality gate verdict: **{qg.get('verdict', 'insufficient_data')}**",
        "",
        "## Projected savings (counterfactual replay of real sessions)",
        "",
        f"- sessions replayed: **{rep.get('sessions', 0)}**",
        f"- sessions by harness: `{rep.get('sessions_by_harness', {})}`",
        f"- golden annotations: **{rep.get('annotated_sessions', 0)}** sessions, "
        f"pending review: **{rep.get('pending_review_sessions', 0)}**, "
        f"stale-context incidents: **{rep.get('stale_context_incidents', 0)}**, "
        f"task parity: `{rep.get('task_parity', {})}`",
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
    if cg and not cg.get("error"):
        lines += [
            f"- context receipts: **{cg.get('receipt_count', 0)}**",
            f"- assertion/admission gaps: **{cg.get('assertion_mismatch_count', 0)}**",
            f"- reread short-circuits: **{cg.get('reread_short_circuit_count', 0)}**",
        ]
    if ts and not ts.get("error"):
        strong = ((ts.get("tiers") or {}).get("strong") or {})
        lines.append(
            f"- tool schema footprint: **{ts.get('original_tokens', 0):,}** tokens "
            f"(strong tier projection: **{strong.get('tokens', 0):,}**)"
        )
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
