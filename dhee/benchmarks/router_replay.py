"""Router replay harness — projects token savings on real session transcripts.

Reads Claude Code or Codex session JSONL files, finds native `Read` /
`Bash` / `Task` tool pairs, and re-runs the corresponding router digest
function to compute what the context would have held if the router had
been active.

This is *counterfactual projection*, not a live A/B. It answers the
question: given the tool calls the model actually made, how many tokens
would the router have saved? It does NOT answer: would the model have
made different tool calls with the router on? That's an A/B and needs a
live session pair.

Reports per-session and aggregate:

    - tool_result raw tokens (actual, from transcript text)
    - tool_result projected tokens (digest length via our renderer)
    - absolute savings, % savings
    - count of tool calls split by tool
    - stale-context incidents and task parity when golden annotations
      are present
    - warnings when source file no longer exists / command can't be
      replayed (we fall back to transcript length for those)

Usage:
    python -m dhee.benchmarks.router_replay
        [--sessions-dir ~/.claude/projects/<slug>]
        [--harness claude_code|codex|all|auto]
        [--golden golden_annotations.jsonl]
        [--limit 5]
        [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dhee.router import agent_digest as _agent_digest
from dhee.router import bash_digest as _bash_digest
from dhee.router import digest as _read_digest

CHARS_PER_TOKEN = 3.5


def _tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) if text else 0


def _flatten_result(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(c.get("text", "") or "")
            elif isinstance(c, str):
                parts.append(c)
        return "".join(parts)
    if content is None:
        return ""
    return json.dumps(content)


@dataclass
class CallProjection:
    tool: str
    raw_tokens: int
    digest_tokens: int
    saved_tokens: int
    note: str = ""

    @property
    def saved_pct(self) -> float:
        if self.raw_tokens <= 0:
            return 0.0
        return (self.saved_tokens / self.raw_tokens) * 100.0


def _parse_boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"pass", "passed", "success", "succeeded", "true", "yes", "1", "parity"}:
        return True
    if text in {"fail", "failed", "failure", "false", "no", "0", "regression"}:
        return False
    return None


def _stale_incident_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value) if "count" not in value else _stale_incident_count(value.get("count"))
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


@dataclass
class SessionReport:
    session_id: str
    harness: str = "claude_code"
    total_calls: int = 0
    calls_by_tool: Counter = field(default_factory=Counter)
    raw_tokens: int = 0
    digest_tokens: int = 0
    saved_tokens: int = 0
    warnings: list[str] = field(default_factory=list)
    annotations_count: int = 0
    stale_context_incidents: int = 0
    task_parity: bool | None = None
    task_parity_score: float | None = None
    pending_review: bool = False
    golden_notes: list[str] = field(default_factory=list)
    # Ground-truth usage, read from each assistant record's `usage` field.
    assistant_turns: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0
    # Tool-result share: tokens returned by tool_use blocks that enter
    # the prompt cache on the next turn.
    tool_result_tokens: int = 0
    # Total tokens across every assistant-visible content block on the
    # user side (approximates total new-input per session).
    user_block_tokens: int = 0

    def add(self, p: CallProjection) -> None:
        self.total_calls += 1
        self.calls_by_tool[p.tool] += 1
        self.raw_tokens += p.raw_tokens
        self.digest_tokens += p.digest_tokens
        if p.note:
            self.warnings.append(p.note)

    def apply_annotation(self, annotation: dict[str, Any]) -> None:
        self.annotations_count += 1
        parity_value = (
            annotation.get("task_parity")
            if "task_parity" in annotation
            else annotation.get("parity")
        )
        parity = _parse_boolish(parity_value)
        if parity is not None:
            self.task_parity = parity
        else:
            review_text = str(
                annotation.get("review_status")
                or annotation.get("status")
                or parity_value
                or ""
            ).strip().lower()
            if review_text in {"needs_review", "pending_review", "pending", "review"}:
                self.pending_review = True
        score = annotation.get("task_parity_score", annotation.get("parity_score"))
        if score is not None:
            try:
                self.task_parity_score = float(score)
            except (TypeError, ValueError):
                pass
        self.stale_context_incidents += _stale_incident_count(
            annotation.get("stale_context_incidents", annotation.get("stale_incidents"))
        )
        note = annotation.get("note") or annotation.get("notes")
        if isinstance(note, list):
            self.golden_notes.extend(str(item) for item in note if item)
        elif note:
            self.golden_notes.append(str(note))

    @property
    def net_saved(self) -> int:
        return self.raw_tokens - self.digest_tokens

    @property
    def saved_pct(self) -> float:
        if self.raw_tokens <= 0:
            return 0.0
        return (self.net_saved / self.raw_tokens) * 100.0

    @property
    def cache_read_per_turn(self) -> float:
        if self.assistant_turns <= 0:
            return 0.0
        return self.cache_read_input_tokens / self.assistant_turns

    @property
    def tool_result_share(self) -> float:
        """Share of cache-read tokens attributable to tool_result blocks."""
        if self.cache_read_input_tokens <= 0:
            return 0.0
        return self.tool_result_tokens / self.cache_read_input_tokens


def _project_read(tool_input: dict[str, Any], result_text: str) -> CallProjection:
    """Project what dhee_read would have returned."""
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    raw = result_text
    raw_tokens = _tokens(raw)

    # Prefer live file — more accurate digest than transcript-derived text.
    live_text = None
    if path:
        try:
            live_text = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            live_text = None
    src = live_text if live_text is not None else raw

    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    range_ = None
    if offset is not None and limit is not None:
        try:
            o = max(1, int(offset))
            n = int(limit)
            range_ = (o, o + n - 1)
        except (TypeError, ValueError):
            range_ = None

    d = _read_digest.digest_read(path or "<unknown>", src, depth="normal", range_=range_)
    # Approximate ptr for render (fake; not stored).
    rendered = d.render("R-replayXXXX")
    digest_tokens = _tokens(rendered)
    note = "" if live_text is not None else "source file missing; digested from transcript"
    return CallProjection(
        tool="Read",
        raw_tokens=raw_tokens,
        digest_tokens=digest_tokens,
        saved_tokens=max(0, raw_tokens - digest_tokens),
        note=note,
    )


def _project_bash(tool_input: dict[str, Any], result_text: str) -> CallProjection:
    """Project what dhee_bash would have returned.

    We don't re-execute the command (non-deterministic, possibly
    destructive). Instead we digest the transcript's stdout-equivalent
    string as if it had been the stdout of that command.
    """
    cmd = tool_input.get("command") or ""
    raw = result_text
    raw_tokens = _tokens(raw)
    d = _bash_digest.digest_bash(
        cmd=cmd,
        exit_code=int(tool_input.get("exit_code", 0) or 0),
        duration_ms=0,
        stdout=raw,
        stderr=str(tool_input.get("stderr") or ""),
    )
    rendered = d.render("B-replayXXXX")
    digest_tokens = _tokens(rendered)
    return CallProjection(
        tool="Bash",
        raw_tokens=raw_tokens,
        digest_tokens=digest_tokens,
        saved_tokens=max(0, raw_tokens - digest_tokens),
        note="output not re-executed; digested from transcript text",
    )


def _project_agent(_tool_input: dict[str, Any], result_text: str) -> CallProjection:
    """Project what dhee_agent would have returned on a subagent result."""
    raw_tokens = _tokens(result_text)
    d = _agent_digest.digest_agent(result_text)
    rendered = d.render("A-replayXXXX")
    digest_tokens = _tokens(rendered)
    return CallProjection(
        tool="Agent",
        raw_tokens=raw_tokens,
        digest_tokens=digest_tokens,
        saved_tokens=max(0, raw_tokens - digest_tokens),
    )


_PROJECTORS = {
    "Read": _project_read,
    "Bash": _project_bash,
    "Task": _project_agent,      # subagent launcher in Claude Code
    "Agent": _project_agent,
}


def _jsonl_records(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _annotation_from_record(rec: dict[str, Any], *, session_id: str) -> dict[str, Any] | None:
    rec_type = rec.get("type") or rec.get("format")
    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
    if rec_type not in {
        "dhee_golden",
        "dhee_golden_replay",
        "golden_annotation",
        "golden_replay",
        "dhee_replay_annotation",
    } and not any(k in payload for k in ("task_parity", "parity", "stale_context_incidents", "stale_incidents")):
        return None
    ann = dict(payload)
    ann.setdefault("session_id", session_id)
    return ann


def load_golden_annotations(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if not path:
        return {}
    files: list[Path]
    if path.is_dir():
        files = sorted(path.glob("*.jsonl"))
    else:
        files = [path]
    out: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        if not f.exists():
            continue
        for rec in _jsonl_records(f):
            ann = _annotation_from_record(rec, session_id=str(rec.get("session_id") or f.stem))
            if ann is None:
                continue
            sid = str(ann.get("session_id") or f.stem)
            out.setdefault(sid, []).append(ann)
    return out


def _detect_harness(path: Path) -> str:
    for rec in _jsonl_records(path):
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        if rec.get("type") in {"response_item", "event_msg"} and payload.get("type"):
            return "codex"
        msg = rec.get("message") or rec
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            return "claude_code"
    return "claude_code"


def _command_text(value: Any) -> str:
    if isinstance(value, list):
        if len(value) >= 3 and value[-2] == "-lc":
            return str(value[-1])
        return " ".join(str(part) for part in value)
    return str(value or "")


def _loads_tool_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {"command": value}
    return data if isinstance(data, dict) else {}


def _normalise_codex_tool(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    lower = str(name or "").lower()
    if lower in {"exec_command", "shell", "bash"}:
        cmd = args.get("cmd") or args.get("command") or args.get("script") or ""
        return "Bash", {"command": _command_text(cmd), **args}
    if lower in {"read_file", "read"}:
        path = args.get("file_path") or args.get("path") or ""
        return "Read", {"file_path": path, **args}
    return None


def _replay_claude_session(
    path: Path,
    *,
    annotations: dict[str, list[dict[str, Any]]] | None = None,
) -> SessionReport:
    """Walk a transcript, pairing tool_use records with their tool_result.

    Also collects ground-truth usage per assistant turn (cache-read,
    cache-creation, output) and tool_result token counts — these feed
    the extended quality-report metrics (cache-read/turn, tool_result
    share).
    """
    pending: dict[str, dict[str, Any]] = {}
    report = SessionReport(session_id=path.stem, harness="claude_code")

    for rec in _jsonl_records(path):
        ann = _annotation_from_record(rec, session_id=path.stem)
        if ann is not None:
            report.apply_annotation(ann)
            continue
        rec_type = rec.get("type")
        msg = rec.get("message") or rec
        # Assistant usage (ground-truth API cache/output counts).
        if rec_type == "assistant" and isinstance(msg, dict):
            usage = msg.get("usage") or {}
            if isinstance(usage, dict):
                report.assistant_turns += 1
                try:
                    report.cache_read_input_tokens += int(
                        usage.get("cache_read_input_tokens", 0) or 0
                    )
                    report.cache_creation_input_tokens += int(
                        usage.get("cache_creation_input_tokens", 0) or 0
                    )
                    report.output_tokens += int(usage.get("output_tokens", 0) or 0)
                except (TypeError, ValueError):
                    pass
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                tid = block.get("id")
                name = block.get("name") or ""
                if tid and name in _PROJECTORS:
                    pending[tid] = {
                        "tool": name,
                        "input": block.get("input") or {},
                    }
            elif btype == "tool_result":
                tid = block.get("tool_use_id")
                text = _flatten_result(block.get("content"))
                # Every tool_result contributes to cache-replay load
                # on subsequent turns, regardless of whether we
                # project a digest for it. Track the raw token mass.
                report.tool_result_tokens += _tokens(text)
                if not tid or tid not in pending:
                    continue
                entry = pending.pop(tid)
                projector = _PROJECTORS[entry["tool"]]
                try:
                    p = projector(entry["input"], text)
                except Exception as exc:  # noqa: BLE001
                    report.warnings.append(
                        f"{entry['tool']} projector failed: {type(exc).__name__}: {exc}"
                    )
                    continue
                report.add(p)
    for ann in (annotations or {}).get(path.stem, []):
        report.apply_annotation(ann)
    return report


def _replay_codex_session(
    path: Path,
    *,
    annotations: dict[str, list[dict[str, Any]]] | None = None,
) -> SessionReport:
    pending: dict[str, dict[str, Any]] = {}
    report = SessionReport(session_id=path.stem, harness="codex")

    for rec in _jsonl_records(path):
        ann = _annotation_from_record(rec, session_id=path.stem)
        if ann is not None:
            report.apply_annotation(ann)
            continue
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        ptype = payload.get("type")
        if rec.get("type") == "response_item" and ptype == "function_call":
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            normalised = _normalise_codex_tool(
                str(payload.get("name") or ""),
                _loads_tool_args(payload.get("arguments")),
            )
            if call_id and normalised:
                tool, tool_input = normalised
                pending[call_id] = {"tool": tool, "input": tool_input}
            continue
        if rec.get("type") == "event_msg" and ptype in {"exec_command_end", "function_call_output", "tool_result"}:
            call_id = str(payload.get("call_id") or payload.get("id") or payload.get("tool_call_id") or "")
            entry = pending.pop(call_id, None)
            if entry is None and ptype == "exec_command_end":
                entry = {"tool": "Bash", "input": {"command": _command_text(payload.get("command"))}}
            if entry is None:
                continue
            text = str(
                payload.get("aggregated_output")
                or payload.get("output")
                or payload.get("stdout")
                or payload.get("content")
                or ""
            )
            if payload.get("stderr"):
                entry["input"]["stderr"] = str(payload.get("stderr") or "")
            if payload.get("exit_code") is not None:
                entry["input"]["exit_code"] = payload.get("exit_code")
            if not entry["input"].get("command") and payload.get("command"):
                entry["input"]["command"] = _command_text(payload.get("command"))
            projector = _PROJECTORS.get(entry["tool"])
            if projector is None:
                continue
            try:
                p = projector(entry["input"], text)
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(
                    f"{entry['tool']} projector failed: {type(exc).__name__}: {exc}"
                )
                continue
            report.add(p)
            report.tool_result_tokens += _tokens(text)
    for ann in (annotations or {}).get(path.stem, []):
        report.apply_annotation(ann)
    return report


def replay_session(
    path: Path,
    *,
    harness: str = "auto",
    annotations: dict[str, list[dict[str, Any]]] | None = None,
) -> SessionReport:
    selected = _detect_harness(path) if harness == "auto" else harness
    if selected in {"codex", "codex_cli"}:
        return _replay_codex_session(path, annotations=annotations)
    return _replay_claude_session(path, annotations=annotations)


def _default_sessions_dir() -> Path:
    # Current project's Claude Code session dir. The one the user is in.
    cwd_slug = "-" + str(Path.cwd()).replace("/", "-").lstrip("-")
    return Path.home() / ".claude" / "projects" / cwd_slug


def _default_codex_sessions_dir() -> Path:
    return Path.home() / ".codex" / "sessions"


def discover_transcripts(
    *,
    sessions_dir: Path | None = None,
    harness: str = "claude_code",
    limit: int = 0,
) -> list[Path]:
    if sessions_dir is not None:
        transcripts = sorted(
            sessions_dir.rglob("*.jsonl") if sessions_dir.is_dir() else [sessions_dir],
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
    elif harness in {"codex", "codex_cli"}:
        root = _default_codex_sessions_dir()
        transcripts = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if root.exists() else []
    elif harness == "all":
        roots = [_default_sessions_dir(), _default_codex_sessions_dir()]
        found: list[Path] = []
        for root in roots:
            if root.exists():
                found.extend(root.rglob("*.jsonl"))
        transcripts = sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        root = _default_sessions_dir()
        transcripts = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if root.exists() else []
    if limit:
        return transcripts[:limit]
    return transcripts


def aggregate_reports(reports: list[SessionReport]) -> dict[str, Any]:
    raw = digest = calls = turns = cache_read = cache_creation = tool_result_tokens = 0
    by_tool: Counter = Counter()
    by_harness: Counter = Counter()
    warnings = 0
    stale = 0
    annotated = 0
    pending_review = 0
    parity_pass = parity_fail = parity_unknown = 0
    parity_scores: list[float] = []
    for r in reports:
        calls += r.total_calls
        by_tool.update(r.calls_by_tool)
        by_harness[r.harness] += 1
        raw += r.raw_tokens
        digest += r.digest_tokens
        warnings += len(r.warnings)
        turns += r.assistant_turns
        cache_read += r.cache_read_input_tokens
        cache_creation += r.cache_creation_input_tokens
        tool_result_tokens += r.tool_result_tokens
        stale += r.stale_context_incidents
        if r.annotations_count:
            annotated += 1
        if r.pending_review:
            pending_review += 1
        if r.task_parity is True:
            parity_pass += 1
        elif r.task_parity is False:
            parity_fail += 1
        else:
            parity_unknown += 1
        if r.task_parity_score is not None:
            parity_scores.append(r.task_parity_score)
    net_saved = raw - digest
    return {
        "sessions": len(reports),
        "sessions_by_harness": dict(by_harness),
        "annotated_sessions": annotated,
        "pending_review_sessions": pending_review,
        "assistant_turns": turns,
        "total_calls": calls,
        "calls_by_tool": dict(by_tool),
        "raw_tokens": raw,
        "digest_tokens": digest,
        "net_saved_tokens": net_saved,
        "saved_pct": round(net_saved / raw * 100, 2) if raw else 0.0,
        "cache_read_tokens_total": cache_read,
        "cache_creation_tokens_total": cache_creation,
        "cache_read_per_turn": int(cache_read / turns) if turns else 0,
        "projected_cache_read_per_turn": int((cache_read - net_saved) / turns) if turns and cache_read else 0,
        "tool_result_tokens": tool_result_tokens,
        "tool_result_share": round(tool_result_tokens / cache_read, 3) if cache_read else 0.0,
        "warnings_count": warnings,
        "stale_context_incidents": stale,
        "task_parity": {
            "pass": parity_pass,
            "fail": parity_fail,
            "unknown": parity_unknown,
            "avg_score": round(sum(parity_scores) / len(parity_scores), 3) if parity_scores else None,
            "score_count": len(parity_scores),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions-dir", type=Path, default=None)
    ap.add_argument("--harness", choices=["claude_code", "codex", "all", "auto"], default="claude_code")
    ap.add_argument("--golden", type=Path, default=None, help="JSONL file or directory with golden replay annotations")
    ap.add_argument("--limit", type=int, default=0, help="Only replay N most-recent sessions")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    transcripts = discover_transcripts(
        sessions_dir=args.sessions_dir,
        harness=args.harness,
        limit=args.limit,
    )
    if args.sessions_dir and not args.sessions_dir.exists():
        print(f"sessions dir not found: {args.sessions_dir}", file=sys.stderr)
        return 2
    annotations = load_golden_annotations(args.golden)
    reports = [replay_session(p, harness=args.harness if args.harness != "all" else "auto", annotations=annotations) for p in transcripts]
    aggregate = aggregate_reports(reports)

    if args.json:
        out = {
            "sessions": [
                {
                    "session_id": r.session_id,
                    "harness": r.harness,
                    "total_calls": r.total_calls,
                    "calls_by_tool": dict(r.calls_by_tool),
                    "raw_tokens": r.raw_tokens,
                    "digest_tokens": r.digest_tokens,
                    "saved_tokens": r.net_saved,
                    "saved_pct": round(r.saved_pct, 2),
                    "annotations_count": r.annotations_count,
                    "stale_context_incidents": r.stale_context_incidents,
                    "task_parity": r.task_parity,
                    "task_parity_score": r.task_parity_score,
                    "pending_review": r.pending_review,
                    "warnings_count": len(r.warnings),
                }
                for r in reports
            ],
            "aggregate": aggregate,
        }
        print(json.dumps(out, indent=2))
        return 0

    # Pretty
    print(f"Harness: {args.harness}")
    print(f"Transcripts: {len(transcripts)}")
    if args.sessions_dir:
        print(f"Sessions dir: {args.sessions_dir}")
    if args.golden:
        print(f"Golden annotations: {args.golden}")
    print(f"Sessions replayed: {len(reports)}")
    print("")
    print(f"{'session':<14} {'harness':<11} {'calls':>6} {'raw_tok':>10} {'digest_tok':>11} {'saved':>10} {'save%':>7} {'stale':>5} {'parity':>7}")
    for r in reports:
        parity = "pass" if r.task_parity is True else ("fail" if r.task_parity is False else "-")
        print(
            f"{r.session_id[:14]:<14} {r.harness:<11} {r.total_calls:>6} {r.raw_tokens:>10,} "
            f"{r.digest_tokens:>11,} {r.net_saved:>10,} {r.saved_pct:>6.1f}% "
            f"{r.stale_context_incidents:>5} {parity:>7}"
        )
    print("")
    print("Aggregate (net = raw - digest):")
    print(f"  harnesses:   {aggregate['sessions_by_harness']}")
    print(f"  calls:       {aggregate['total_calls']}")
    print(f"  by tool:     {aggregate['calls_by_tool']}")
    print(f"  raw tokens:  {aggregate['raw_tokens']:,}")
    print(f"  digest:      {aggregate['digest_tokens']:,}")
    print(f"  net saved:   {aggregate['net_saved_tokens']:,}  ({aggregate['saved_pct']:.1f}%)")
    print(f"  stale ctx:   {aggregate['stale_context_incidents']}")
    print(f"  parity:      {aggregate['task_parity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
