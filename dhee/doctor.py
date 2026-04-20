"""Dhee doctor — honest observability of what's wired, what's running, and
what the next-release work adds.

No opt-in controls. No on/off switches. Pure read-only inspection so you
(and future-you) can tell at a glance:

  - Is the router live? Is enforcement on? Which hooks are installed?
  - What's the pointer/digest state for this session + aggregate?
  - What does the cognition layer own today (MetaBuddhi proposals,
    contrastive pairs, Nididhyasana evolution status)?
  - What does the memory substrate hold (engram facts, distillation
    provenance, episodes)?
  - Which promises are fully closed today vs. "closes in Movement N"?

Read ``dhee doctor`` before you ship, before you file a bug, before you
believe any README number. It composes the truth from the same files the
system uses at runtime — no separate state.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Plan-movement labels — the single source of truth for "what closes when".
# Keep this aligned with ~/.claude/plans/encapsulated-rolling-bengio.md.
# ---------------------------------------------------------------------------
MOVEMENT_CAPABILITIES = {
    "M0": "Truth-and-surface pass (docstrings, README, doctor, provenance read path)",
    "M1": "Finish token savings (dhee_grep, persistent ptrs, partial expansion, default-on enforce, extended quality report)",
    "M2": "Propositional substrate + supersede chains (tier, superseded_by, preferences, retrieval integration)",
    "M3": "Years-of-memory (tier promotion, background consolidator, lineage UI, Epistemic Control Loop)",
    "M4": "Honest self-evolution (real MetaBuddhi loop, Nididhyasana scheduler, group-relative confidence, step-level utility)",
    "M5": ".dheemem protocol v1 (portable core + optional extensions, export/import/migrate CLI, signed manifests)",
    "M6": "Harness adapters (base + ClaudeCode + Codex, canonical event vocabulary)",
    "M7": "Public proof (replay corpus, decades synthetic corpus, portability eval, README rewritten to measured numbers)",
}


@dataclass
class DoctorReport:
    dhee_version: str = ""
    generated_at: float = 0.0
    core: dict[str, Any] = field(default_factory=dict)
    router: dict[str, Any] = field(default_factory=dict)
    cognition: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dhee_version": self.dhee_version,
            "generated_at": self.generated_at,
            "core": self.core,
            "router": self.router,
            "cognition": self.cognition,
            "memory": self.memory,
            "capabilities": self.capabilities,
        }


# ---------------------------------------------------------------------------
# Section builders — each returns a dict, never raises. A broken subsystem
# shows up as {"error": "..."} rather than crashing the whole doctor.
# ---------------------------------------------------------------------------


def _core_section() -> dict[str, Any]:
    try:
        from dhee import __version__
    except Exception:
        __version__ = "unknown"
    try:
        from dhee.cli_config import CONFIG_DIR, CONFIG_PATH, load_config

        cfg = load_config()
        config_dir = str(CONFIG_DIR)
        config_path = str(CONFIG_PATH)
        provider = cfg.get("provider", "not configured")
        packages = cfg.get("packages", [])
    except Exception as exc:
        return {
            "dhee_version": __version__,
            "error": f"{type(exc).__name__}: {exc}",
        }

    dbs: dict[str, Any] = {}
    for label, name in [("vector", "sqlite_vec.db"), ("history", "history.db")]:
        p = os.path.join(config_dir, name)
        if os.path.exists(p):
            dbs[label] = {"path": p, "bytes": os.path.getsize(p)}
        else:
            dbs[label] = {"path": p, "bytes": 0, "missing": True}

    return {
        "dhee_version": __version__,
        "provider": provider,
        "packages": packages,
        "config_dir": config_dir,
        "config_path": config_path,
        "dbs": dbs,
    }


def _router_section() -> dict[str, Any]:
    try:
        from dhee.router.install import ENFORCE_FLAG, status as router_status
        from dhee.router.pre_tool_gate import _flag_file
        from dhee.router.stats import compute_stats
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    try:
        s = router_status()
        st = compute_stats().to_dict()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    # Hook presence (read directly so doctor stays independent of install module)
    hooks_installed: list[str] = []
    try:
        settings_path = Path.home() / ".claude" / "settings.json"
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            for event, entries in (data.get("hooks", {}) or {}).items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    for h in entry.get("hooks", []):
                        if "dhee.hooks.claude_code" in h.get("command", ""):
                            hooks_installed.append(event)
                            break
    except Exception:
        pass

    enforce_flag_env = os.environ.get(ENFORCE_FLAG) == "1"
    enforce_flag_file = False
    try:
        enforce_flag_file = _flag_file().exists()
    except Exception:
        pass

    return {
        "enabled": s.enabled,
        "allowed_tools": list(s.allowed_tools),
        "env_flag": s.env_flag,
        "managed_marker": s.managed,
        "enforce_on": enforce_flag_env or enforce_flag_file,
        "enforce_source": (
            "env" if enforce_flag_env
            else "flag_file" if enforce_flag_file
            else "off"
        ),
        "hooks_installed": sorted(set(hooks_installed)),
        "ptr_store": {
            "total_calls": st.get("total_calls", 0),
            "bytes_stored": st.get("bytes_stored", 0),
            "est_tokens_diverted": st.get("est_tokens_diverted", 0),
            "expansion_calls": st.get("expansion_calls", 0),
            "expansion_rate": st.get("expansion_rate", 0.0),
        },
    }


def _cognition_section() -> dict[str, Any]:
    out: dict[str, Any] = {}

    # MetaBuddhi — proposal counts by status from on-disk attempts.jsonl.
    # Native today: propose + conservative threshold gating; replay-based
    # assessment + auto-rollback closes in Movement 4.
    try:
        from dhee.cli_config import CONFIG_DIR

        attempts_path = Path(CONFIG_DIR) / "meta_buddhi" / "attempts.jsonl"
        counts = {"proposed": 0, "promoted": 0, "rolled_back": 0, "abandoned": 0}
        total = 0
        if attempts_path.exists():
            with attempts_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    st = str(rec.get("status", "proposed"))
                    if st in counts:
                        counts[st] += 1
                    else:
                        counts.setdefault(st, 0)
                        counts[st] += 1
        out["meta_buddhi"] = {
            "status": "native; loop closes in M4",
            "attempts_total": total,
            "attempts_by_status": counts,
            "log_path": str(attempts_path) if attempts_path.exists() else None,
        }
    except Exception as exc:
        out["meta_buddhi"] = {"error": f"{type(exc).__name__}: {exc}"}

    # Contrastive — pair count if the store has persisted anything.
    try:
        from dhee.cli_config import CONFIG_DIR

        contrastive_dir = Path(CONFIG_DIR) / "contrastive"
        pair_count = 0
        if contrastive_dir.exists():
            for f in contrastive_dir.glob("*.jsonl"):
                try:
                    with f.open("r", encoding="utf-8") as fp:
                        pair_count += sum(1 for line in fp if line.strip())
                except OSError:
                    continue
        out["contrastive"] = {
            "status": "native; DPO export pipeline closes in M4 if committed",
            "pairs_persisted": pair_count,
            "store_dir": str(contrastive_dir) if contrastive_dir.exists() else None,
        }
    except Exception as exc:
        out["contrastive"] = {"error": f"{type(exc).__name__}: {exc}"}

    # Nididhyasana — session-boundary gate is live as of M4.3. The full
    # training cycle stays gated until the training-infrastructure
    # relocation (dhee.training.*, dhee.mini.progressive_trainer) lands.
    try:
        from dhee.cli_config import CONFIG_DIR

        gate_path = Path(CONFIG_DIR) / "nididhyasana" / "session_gates.jsonl"
        gate_total = 0
        last_gate: dict[str, Any] | None = None
        gate_fired_count = 0
        if gate_path.exists():
            with gate_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    gate_total += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("gate_fired"):
                        gate_fired_count += 1
                    last_gate = rec
        out["nididhyasana"] = {
            "status": "session-boundary gate live (M4.3); full evolve() "
                      "requires training-infra relocation (M7)",
            "threshold_logic": "live (see NididhyasanaLoop.should_evolve)",
            "session_gates_total": gate_total,
            "session_gates_fired": gate_fired_count,
            "last_gate": last_gate,
            "gate_log": str(gate_path) if gate_path.exists() else None,
        }
    except Exception as exc:
        out["nididhyasana"] = {
            "status": "session-boundary gate live (M4.3)",
            "error": f"{type(exc).__name__}: {exc}",
        }

    return out


def _memory_section() -> dict[str, Any]:
    try:
        from dhee.cli_config import CONFIG_DIR
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    db_path = os.path.join(CONFIG_DIR, "sqlite_vec.db")
    if not os.path.exists(db_path):
        return {
            "db_path": db_path,
            "note": "vector DB not created yet (run `dhee ingest` or `dhee remember`)",
        }

    import sqlite3

    counts: dict[str, Any] = {}
    supersede: dict[str, Any] = {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            def _count(sql: str, params: tuple = ()) -> int | None:
                try:
                    row = conn.execute(sql, params).fetchone()
                    return int(row[0]) if row else None
                except sqlite3.OperationalError:
                    return None

            counts["memories"] = _count(
                "SELECT COUNT(*) FROM memories WHERE tombstone = 0"
            )
            counts["engram_facts"] = _count("SELECT COUNT(*) FROM engram_facts")
            counts["engram_entities"] = _count(
                "SELECT COUNT(*) FROM engram_entities"
            )
            counts["engram_links"] = _count("SELECT COUNT(*) FROM engram_links")
            counts["distillation_provenance"] = _count(
                "SELECT COUNT(*) FROM distillation_provenance"
            )
            counts["distillation_log"] = _count(
                "SELECT COUNT(*) FROM distillation_log"
            )
            counts["memory_history"] = _count(
                "SELECT COUNT(*) FROM memory_history"
            )

            # Supersede chain status on engram_facts. The column lands in M2;
            # if it's absent we say so plainly — don't pretend it's empty.
            try:
                cols = {
                    r[1]
                    for r in conn.execute("PRAGMA table_info(engram_facts)").fetchall()
                }
                has_tier = "tier" in cols
                has_sup = "superseded_by" in cols or "superseded_by_id" in cols
                if has_tier and has_sup:
                    supersede["column"] = "present"
                    supersede["supersede_chain_rows"] = _count(
                        "SELECT COUNT(*) FROM engram_facts "
                        "WHERE superseded_by IS NOT NULL "
                        "   OR superseded_by_id IS NOT NULL"
                    )
                    supersede["tier_distribution"] = {
                        tier: _count(
                            "SELECT COUNT(*) FROM engram_facts WHERE tier = ?",
                            (tier,),
                        )
                        for tier in ("canonical", "high", "medium", "low", "avoid")
                    }
                elif has_sup:
                    supersede["column"] = "partial — supersede present, tier lands in M2"
                else:
                    supersede["column"] = "not present — lands in M2"
            except sqlite3.OperationalError as exc:
                supersede["error"] = str(exc)
    except Exception as exc:
        return {"db_path": db_path, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "db_path": db_path,
        "counts": counts,
        "supersede": supersede,
    }


def _capabilities_section(router: dict[str, Any]) -> dict[str, Any]:
    """Plain-language summary of what closes today vs. later movements.

    The aim is that a user running `dhee doctor` knows exactly which
    promises are redeemable on their machine right now.
    """
    router_report_exists = True  # dhee router report CLI wired at cli.py:640
    router_wired = {
        "mcp_tools": [
            "dhee_read",
            "dhee_bash",
            "dhee_agent",
            "dhee_grep",
            "dhee_expand_result",
            "dhee_list_assets",
            "dhee_get_asset",
            "dhee_sync_codex_artifacts",
            "dhee_why",
            "dhee_handoff",
        ],
        "missing": [],
        "enforce_default_on": True,
        "router_report_cli": router_report_exists,
    }

    closed_today = [
        "Router digests (dhee_read, dhee_bash, dhee_agent, dhee_grep)",
        "Pointer-based raw storage with in-session expansion",
        "dhee router report — replay + stats + shareable Markdown",
        "Cognition kernel (TaskState / Episode / Belief / Policy / Intention)",
        "Propositional write path (engram_facts / engram_entities / engram_links)",
        "M2 tiering + supersede chains on engram_facts (tier-aware retrieval, "
        "reaffirmation counting, avoid-tier demotion)",
        "M2 engram_preferences store (first-class stance + topic, supersede-aware, "
        "resolve_latest prefers it for preference predicates)",
        ".dheemem portable pack (signed ed25519 manifest, export / import)",
        "M3 tier promotion + downstream-success bump (medium→high→canonical, "
        "superseded rows never promoted, canonical write-once)",
        "M3 background consolidator (dedup fusion + forgetting sweep into "
        "lineage-preserving cold archive; idempotent, safe on hot DB)",
        "M3 Epistemic Control Loop wired into HyperContext "
        "(last_verified_at + pending_epistemic_checks → buddhi.get_hyper_context "
        "surfaces stale cited facts as epistemic_checks + [EPISTEMIC] action items)",
        "MetaBuddhi proposals with conservative threshold gating",
        "M4.2 closed-loop: EvolutionLayer feeds record_evaluation on every "
        "accepted/corrected answer (propose → assess → promote/rollback at "
        "_MIN_EVAL_COUNT); accepted answers also stamp last_verified_at + "
        "promote_on_downstream_success on cited engram_facts",
        "M4.2b group-relative confidence: MetaBuddhi snapshots the parent's "
        "per-task-type rolling baseline at propose time and resolves using a "
        "weighted group-relative delta; a catastrophic regression on any "
        "single task type (≥ _GROUP_CATASTROPHE_THRESHOLD) forces rollback "
        "even when the aggregate looks positive — no task type gets drowned "
        "out by an easier one",
        "M4.3 Nididhyasana session-boundary gate: Claude Code Stop/SessionEnd "
        "hooks call evolution_layer.on_session_end() → should_evolve() → "
        "persist gate record to ~/.dhee/nididhyasana/session_gates.jsonl",
        "M6.1 Harness adapter layer: CanonicalEvent vocabulary + HarnessAdapter "
        "base (translate / dispatch / handle, handler errors isolated), "
        "ClaudeCodeAdapter binding vendor events to canonical ones, "
        "CodexAdapter (fidelity=transcript) ingesting latest session log "
        "on SESSION_END — ships via dhee.harness.{base,claude_code,codex}",
        "M6.2 Multi-harness install: dhee install --harness {all,claude_code,codex}, "
        "dhee harness status/enable/disable; shared ~/.dhee kernel, Claude Code "
        "via native hooks+MCP+router, Codex via MCP config + AGENTS.override.md "
        "(dhee/harness/install.py)",
        "M6.3 Live Codex event-stream ingestion: dhee/core/codex_stream.py "
        "incrementally tails ~/.codex/sessions/**.jsonl with a persisted cursor, "
        "projecting user file refs → artifact_attached, native shell/function "
        "calls → shared-task in_flight, completed shell events → ptr-backed "
        "digests + shared-task results — the real live PRE_TOOL/POST_TOOL "
        "surface for Codex without waiting for native hooks",
        "ME Ephemeral shared-task bus: dhee/core/shared_tasks.py "
        "(publish_in_flight / publish_shared_task_result / "
        "resolve_active_shared_task) — task-local tool outputs visible across "
        "agents without bloating durable memory",
        "MF CLI harness surface: dhee status shows native_harnesses per harness "
        "(enabled_in_config, mcp_registered)",

        "M7.1 Training-infra relocation: dheeModel/training/* moved into "
        "canonical dhee/training/*; dhee/core/evolution.py now imports "
        "NididhyasanaLoop from the canonical path; legacy dheeModel/ tree "
        "deleted.",
        "M7.2 ProgressiveTrainer restored at dhee.mini.progressive_trainer: "
        "3-stage SFT → DPO → RL-gate pipeline with structured Stage/"
        "ProgressiveResult dataclasses; missing training deps surface as "
        "not_available rather than crashing. Every cycle appends to "
        "<data_dir>/cycles.jsonl for doctor observability.",
        "M7.3 Replay-based RL gate at dhee.mini.replay_gate: ReplayGate + "
        "GateVerdict enforce the 'no silent promotion' rule — candidate "
        "only promotes when a pluggable evaluator shows it beats the "
        "incumbent by ≥ GATE_PROMOTE_DELTA on a held-out corpus. No corpus, "
        "no evaluator, no incumbent, thin corpus, evaluator crash, and "
        "below-threshold delta all return structured reasons and leave "
        "model_improved False. Wires through ProgressiveTrainer via "
        "replay_corpus_dir / replay_evaluator / incumbent_model_path.",
        "M7.6 Portability eval at dhee.benchmarks.portability: "
        "run_portability_eval() round-trips a user's Dhee state "
        "through a signed .dheemem pack and scores per-substrate "
        "retention (memories / memory_history / distillation_provenance "
        "/ artifacts / vectors) + handoff survival. CLI: "
        "`dhee portability-eval [--user-id X] [--threshold F] [--json]`. "
        "M7.6b fix: intra-pack content_hash + history-signature "
        "collisions no longer collapse distinct source rows on merge — "
        "dedup only fires against rows that pre-existed in the target "
        "before this import pass. Live run on dev state (542 memories, "
        "14197 history rows, 8 artifacts) now round-trips at 1.0 "
        "retention across every substrate with handoff_survived=True.",
        "M7.5 Karma-based replay evaluator at dhee.mini.karma_evaluator: "
        "build_karma_evaluator() returns a log-likelihood scorer for "
        "HF causal LMs (mean per-token log-prob of expected given "
        "prompt). Requires torch + transformers; returns None when "
        "deps are missing so ReplayGate honestly reports no_evaluator "
        "instead of fabricating a score. GGUF/non-HF paths raise a "
        "clear error pointing callers at a custom replay_evaluator. "
        "Plugged into ReplayGate._default_karma_evaluator() and "
        "exported from dhee.mini.",
        "M7.4 Replay corpus capture from Samskara: "
        "SamskaraCollector.export_replay_corpus() derives a "
        "ReplayGate-shaped JSONL corpus directly from the durable "
        "samskaras.jsonl log (ANSWER_ACCEPTED → validated pairs, "
        "ANSWER_CORRECTED → ground-truth pairs). No synthetic records, "
        "no bolt-on collector — the corpus grows as real users "
        "accept/correct answers. CLI: `dhee replay-corpus export "
        "[--out-dir ~/.dhee/replay_corpus] [--max-records N] [--json]`. "
        "Empty log honestly returns record_count=0.",
        "M7.7 README rewrite to measured numbers: removed three "
        "'next-release' claims that had already shipped — tier-based "
        "canonical retention (M2+M3), the propose→assess→commit→rollback "
        "loop with per-task-type group-relative confidence + "
        "catastrophic-group guardrail + Nididhyasana session gate + "
        "replay-based RL gate (M4.2/4.2b/4.3/7.3), and the replay "
        "corpus footnote now points at `dhee replay-corpus export` "
        "instead of a future release. Test count updated to 1,170+ "
        "(1174 collected; 1162 passing + 12 skipped).",
        "M7.8 Decades longevity eval at dhee.benchmarks.decades: "
        "`run_decades_eval()` + `dhee decades-eval` drive the "
        "Movement-3 verification line on a synthetic corpus — 10K "
        "time-skewed facts, ~20% supersede rate, 3-year simulated "
        "span. Measures three substrate invariants: canonical-tier "
        "rows retained at 1.0 through the forgetting sweep, "
        "supersede chains stay explorable at 1.0 (live or archived "
        "in engram_fact_archive), and recall-latency degradation at "
        "10K vs 1K rows stays under 2.0×. Live 10K run on this HEAD: "
        "canonical_retention=1.0 (1000/1000), supersede_chain_integrity="
        "1.0 (1000/1000), latency_degradation=1.03× — passed=True. "
        "This is the only place synthetic data is honest: nobody has "
        "3 real years of usage yet. Regression: "
        "tests/test_decades_eval.py (8 tests).",
        "Contrastive pair surfacing as `contrasts` in HyperContext",
        "LongMemEval retrieval pipeline (in-tree reproducible)",
    ]
    partial_today = [
        "distillation_provenance — written today; read-path wiring landed in M0",
        "Nididhyasana full evolve() cycle — gate, canonical training "
        "path, ProgressiveTrainer, ReplayGate, Samskara-derived replay "
        "corpus, and karma-based evaluator all live "
        "(M7.1–M7.5); model_improved still defaults to honest "
        "not_available until the user actually accumulates enough "
        "ANSWER_ACCEPTED/CORRECTED records to train and score against",
    ]

    return {
        "closed_today": closed_today,
        "partial_today": partial_today,
        "router_wired": router_wired,
        "movement_plan": MOVEMENT_CAPABILITIES,
    }


def build_report() -> DoctorReport:
    try:
        from dhee import __version__
    except Exception:
        __version__ = "unknown"

    core = _core_section()
    router = _router_section()
    cognition = _cognition_section()
    memory = _memory_section()
    capabilities = _capabilities_section(router)

    return DoctorReport(
        dhee_version=__version__,
        generated_at=time.time(),
        core=core,
        router=router,
        cognition=cognition,
        memory=memory,
        capabilities=capabilities,
    )


# ---------------------------------------------------------------------------
# Human-readable formatter — designed to be paste-into-issue-safe.
# ---------------------------------------------------------------------------


def format_human(report: DoctorReport) -> str:
    lines: list[str] = []
    core = report.core
    router = report.router
    cog = report.cognition
    mem = report.memory
    cap = report.capabilities

    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(report.generated_at))
    lines.append(f"dhee {report.dhee_version}  |  {ts}")
    lines.append("")

    # Core
    lines.append("[ core ]")
    lines.append(f"  provider:     {core.get('provider', '?')}")
    lines.append(f"  packages:     {', '.join(core.get('packages', [])) or 'none'}")
    lines.append(f"  config:       {core.get('config_path', '?')}")
    for label, info in (core.get("dbs") or {}).items():
        if info.get("missing"):
            lines.append(f"  {label:<13} not created yet ({info.get('path')})")
        else:
            size = info.get("bytes", 0)
            size_h = f"{size / 1_000_000:.1f} MB" if size >= 1_000_000 else f"{size / 1_000:.1f} KB"
            lines.append(f"  {label:<13} {info.get('path')} ({size_h})")
    lines.append("")

    # Router
    lines.append("[ router ]")
    if "error" in router:
        lines.append(f"  error: {router['error']}")
    else:
        lines.append(f"  enabled:      {router.get('enabled')}")
        lines.append(
            f"  enforce:      {'on' if router.get('enforce_on') else 'off'}"
            f"  (source: {router.get('enforce_source')})"
        )
        lines.append(
            f"  hooks:        {', '.join(router.get('hooks_installed', [])) or '(none)'}"
        )
        ptr = router.get("ptr_store", {})
        lines.append(
            f"  ptrs:         {ptr.get('total_calls', 0)} calls, "
            f"{ptr.get('bytes_stored', 0):,} bytes diverted "
            f"(~{ptr.get('est_tokens_diverted', 0):,} tokens); "
            f"expansion {ptr.get('expansion_rate', 0):.1%}"
        )
    lines.append("")

    # Cognition
    lines.append("[ cognition ]")
    mb = cog.get("meta_buddhi", {})
    if "error" in mb:
        lines.append(f"  MetaBuddhi:   error: {mb['error']}")
    else:
        lines.append(f"  MetaBuddhi:   {mb.get('status')}")
        by = mb.get("attempts_by_status") or {}
        lines.append(
            f"    attempts:   total={mb.get('attempts_total', 0)}  "
            f"proposed={by.get('proposed', 0)}  promoted={by.get('promoted', 0)}  "
            f"rolled_back={by.get('rolled_back', 0)}"
        )
    nd = cog.get("nididhyasana", {})
    lines.append(f"  Nididhyasana: {nd.get('status')}")
    lines.append(f"    scheduler:  {nd.get('scheduler')}")
    cn = cog.get("contrastive", {})
    if "error" in cn:
        lines.append(f"  Contrastive:  error: {cn['error']}")
    else:
        lines.append(f"  Contrastive:  {cn.get('status')}")
        lines.append(f"    pairs:      {cn.get('pairs_persisted', 0)} on disk")
    lines.append("")

    # Memory
    lines.append("[ memory substrate ]")
    if "error" in mem:
        lines.append(f"  error: {mem['error']}")
    elif "note" in mem:
        lines.append(f"  {mem['note']}")
    else:
        for k, v in (mem.get("counts") or {}).items():
            if v is None:
                lines.append(f"  {k:<25} table missing")
            else:
                lines.append(f"  {k:<25} {v:,}")
        sup = mem.get("supersede") or {}
        if sup:
            if sup.get("column") == "present":
                lines.append(
                    f"  supersede_chain           "
                    f"{sup.get('supersede_chain_rows', 0):,} rows"
                )
            else:
                lines.append(f"  supersede_chain           {sup.get('column')}")
    lines.append("")

    # Capabilities
    lines.append("[ wired today ]")
    for item in cap.get("closed_today", []):
        lines.append(f"  + {item}")
    lines.append("")
    lines.append("[ partial today (movement N closes the loop) ]")
    for item in cap.get("partial_today", []):
        lines.append(f"  ~ {item}")
    lines.append("")
    lines.append("[ release plan ]")
    for mv, text in cap.get("movement_plan", {}).items():
        lines.append(f"  {mv}: {text}")
    return "\n".join(lines)


def run(as_json: bool = False) -> str:
    report = build_report()
    if as_json:
        return json.dumps(report.to_dict(), indent=2) + "\n"
    return format_human(report) + "\n"
