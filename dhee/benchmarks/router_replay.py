"""Router replay harness — projects token savings on real session transcripts.

Reads Claude Code session JSONL files, finds each native `Read` / `Bash`
/ `Task` tool_use + tool_result pair, and re-runs the corresponding
router digest function to compute what the context would have held if
the router had been active.

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
    - warnings when source file no longer exists / command can't be
      replayed (we fall back to transcript length for those)

Usage:
    python -m dhee.benchmarks.router_replay
        [--sessions-dir ~/.claude/projects/<slug>]
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


@dataclass
class SessionReport:
    session_id: str
    total_calls: int = 0
    calls_by_tool: Counter = field(default_factory=Counter)
    raw_tokens: int = 0
    digest_tokens: int = 0
    saved_tokens: int = 0
    warnings: list[str] = field(default_factory=list)
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
        exit_code=0,
        duration_ms=0,
        stdout=raw,
        stderr="",
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


def replay_session(path: Path) -> SessionReport:
    """Walk a transcript, pairing tool_use records with their tool_result.

    Also collects ground-truth usage per assistant turn (cache-read,
    cache-creation, output) and tool_result token counts — these feed
    the extended quality-report metrics (cache-read/turn, tool_result
    share).
    """
    pending: dict[str, dict[str, Any]] = {}
    report = SessionReport(session_id=path.stem)

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
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
    return report


def _default_sessions_dir() -> Path:
    # Current project's Claude Code session dir. The one the user is in.
    cwd_slug = "-" + str(Path.cwd()).replace("/", "-").lstrip("-")
    return Path.home() / ".claude" / "projects" / cwd_slug


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions-dir", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=0, help="Only replay N most-recent sessions")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sdir = args.sessions_dir or _default_sessions_dir()
    if not sdir.exists():
        print(f"sessions dir not found: {sdir}", file=sys.stderr)
        return 2

    transcripts = sorted(sdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if args.limit:
        transcripts = transcripts[: args.limit]

    reports = [replay_session(p) for p in transcripts]

    # Aggregate — compute saved as raw - digest honestly (some calls
    # produce digests larger than raw for tiny inputs; those increase
    # digest_tokens and reduce net savings).
    agg = SessionReport(session_id="__aggregate__")
    for r in reports:
        agg.total_calls += r.total_calls
        agg.calls_by_tool.update(r.calls_by_tool)
        agg.raw_tokens += r.raw_tokens
        agg.digest_tokens += r.digest_tokens
    agg.saved_tokens = agg.raw_tokens - agg.digest_tokens

    if args.json:
        out = {
            "sessions": [
                {
                    "session_id": r.session_id,
                    "total_calls": r.total_calls,
                    "calls_by_tool": dict(r.calls_by_tool),
                    "raw_tokens": r.raw_tokens,
                    "digest_tokens": r.digest_tokens,
                    "saved_tokens": r.net_saved,
                    "saved_pct": round(r.saved_pct, 2),
                    "warnings_count": len(r.warnings),
                }
                for r in reports
            ],
            "aggregate": {
                "sessions": len(reports),
                "total_calls": agg.total_calls,
                "calls_by_tool": dict(agg.calls_by_tool),
                "raw_tokens": agg.raw_tokens,
                "digest_tokens": agg.digest_tokens,
                "saved_tokens": agg.net_saved,
                "saved_pct": round(agg.saved_pct, 2),
            },
        }
        print(json.dumps(out, indent=2))
        return 0

    # Pretty
    print(f"Sessions dir: {sdir}")
    print(f"Sessions replayed: {len(reports)}")
    print("")
    print(f"{'session':<14} {'calls':>6} {'raw_tok':>10} {'digest_tok':>11} {'saved':>10} {'save%':>7}")
    for r in reports:
        print(
            f"{r.session_id[:14]:<14} {r.total_calls:>6} {r.raw_tokens:>10,} "
            f"{r.digest_tokens:>11,} {r.net_saved:>10,} {r.saved_pct:>6.1f}%"
        )
    print("")
    print("Aggregate (net = raw - digest):")
    print(f"  calls:       {agg.total_calls}")
    print(f"  by tool:     {dict(agg.calls_by_tool)}")
    print(f"  raw tokens:  {agg.raw_tokens:,}")
    print(f"  digest:      {agg.digest_tokens:,}")
    print(f"  net saved:   {agg.net_saved:,}  ({agg.saved_pct:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
