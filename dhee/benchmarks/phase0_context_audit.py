"""Phase 0 — Honest baseline of where tokens go in Claude Code sessions.

Reads Claude Code session transcripts (JSONL in ~/.claude/projects/<slug>/)
and produces two reports:

    1. Ground-truth API token usage from each assistant record's `usage`
       field. This is what Anthropic actually charged us — not a heuristic.
    2. Content-category estimation from transcript bodies. Rough, but the
       only way to split "user prompt vs tool result vs Dhee injection"
       since the API usage field only reports aggregate input tokens.

Outputs:
    - Per-session summary table on stdout
    - Aggregate category breakdown on stdout
    - `runs/phase0/sessions.csv` with one row per session
    - `runs/phase0/turns.csv` with one row per assistant turn

Run:
    python -m dhee.benchmarks.phase0_context_audit
        [--sessions-dir ~/.claude/projects/-Users-chitranjanmalviya-Desktop-Dhee]
        [--out-dir runs/phase0]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 3.5
DHEE_MARKERS = ("<dhee>", "<dhee:", "[Engram \u2014", "[Engram -", "relevant memories from previous sessions")


def token_estimate(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) if text else 0


def detect_dhee_injection(text: str) -> bool:
    return any(m in text for m in DHEE_MARKERS)


def flatten_block_text(block: Any) -> str:
    """Return the text content of a content block, or the stringified form."""
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    btype = block.get("type")
    if btype == "text":
        return block.get("text", "") or ""
    if btype == "thinking":
        return block.get("thinking", "") or ""
    if btype == "tool_use":
        return json.dumps(block.get("input", {}))
    if btype == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            return "".join(flatten_block_text(c) for c in content)
        if isinstance(content, str):
            return content
        return json.dumps(content) if content is not None else ""
    return ""


def classify(role: str, block: Any) -> str:
    """Bucket a content block into a reporting category."""
    if isinstance(block, str):
        return f"{role}_string"
    if not isinstance(block, dict):
        return f"{role}_unknown"
    btype = block.get("type", "unknown")
    if btype == "tool_result":
        text = flatten_block_text(block)
        if detect_dhee_injection(text):
            return "dhee_injection"
        return "tool_result"
    if btype == "tool_use":
        return "tool_use"
    if btype == "thinking":
        return "asst_thinking"
    if btype == "text":
        return f"{role}_text"
    return f"{role}_{btype}"


def analyze_session(path: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "session_id": path.stem,
        "size_bytes": path.stat().st_size,
        "records": 0,
        "turns_assistant": 0,
        "turns_user": 0,
        "api_input_tokens": 0,
        "api_cache_creation": 0,
        "api_cache_read": 0,
        "api_output_tokens": 0,
        "category_tokens": defaultdict(int),
        "category_chars": defaultdict(int),
        "dhee_injection_hits": 0,
        "dhee_injection_chars": 0,
        "first_turn_cache_creation": None,
        "gitBranch": None,
        "version": None,
        "turns": [],
    }

    first_asst = True
    with path.open() as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            stats["records"] += 1
            if stats["gitBranch"] is None:
                stats["gitBranch"] = r.get("gitBranch")
            if stats["version"] is None:
                stats["version"] = r.get("version")

            typ = r.get("type")
            msg = r.get("message") if isinstance(r.get("message"), dict) else {}

            if typ == "assistant":
                stats["turns_assistant"] += 1
                usage = msg.get("usage", {}) or {}
                in_tok = int(usage.get("input_tokens", 0))
                cc_tok = int(usage.get("cache_creation_input_tokens", 0))
                cr_tok = int(usage.get("cache_read_input_tokens", 0))
                out_tok = int(usage.get("output_tokens", 0))

                stats["api_input_tokens"] += in_tok
                stats["api_cache_creation"] += cc_tok
                stats["api_cache_read"] += cr_tok
                stats["api_output_tokens"] += out_tok

                if first_asst:
                    stats["first_turn_cache_creation"] = cc_tok
                    first_asst = False

                stats["turns"].append({
                    "turn_idx": stats["turns_assistant"],
                    "input_tokens": in_tok,
                    "cache_creation": cc_tok,
                    "cache_read": cr_tok,
                    "output_tokens": out_tok,
                    "new_input_total": in_tok + cc_tok,
                })

                content = msg.get("content")
                if isinstance(content, list):
                    for b in content:
                        cat = classify("asst", b)
                        text = flatten_block_text(b)
                        stats["category_tokens"][cat] += token_estimate(text)
                        stats["category_chars"][cat] += len(text)

            elif typ == "user":
                stats["turns_user"] += 1
                content = msg.get("content") if isinstance(msg, dict) else r.get("content")
                if isinstance(content, list):
                    for b in content:
                        cat = classify("user", b)
                        text = flatten_block_text(b)
                        stats["category_tokens"][cat] += token_estimate(text)
                        stats["category_chars"][cat] += len(text)
                        if cat == "dhee_injection":
                            stats["dhee_injection_hits"] += 1
                            stats["dhee_injection_chars"] += len(text)
                elif isinstance(content, str):
                    cat = "dhee_injection" if detect_dhee_injection(content) else "user_string"
                    stats["category_tokens"][cat] += token_estimate(content)
                    stats["category_chars"][cat] += len(content)
                    if cat == "dhee_injection":
                        stats["dhee_injection_hits"] += 1
                        stats["dhee_injection_chars"] += len(content)

    stats["category_tokens"] = dict(stats["category_tokens"])
    stats["category_chars"] = dict(stats["category_chars"])
    return stats


def write_session_csv(all_stats: list[dict[str, Any]], out_path: Path) -> None:
    cats_seen: set[str] = set()
    for s in all_stats:
        cats_seen.update(s["category_tokens"].keys())
    cat_cols = sorted(cats_seen)

    fields = [
        "session_id", "records", "turns_assistant", "turns_user",
        "api_input_tokens", "api_cache_creation", "api_cache_read",
        "api_output_tokens", "first_turn_cache_creation",
        "dhee_injection_hits", "dhee_injection_chars",
        "gitBranch", "version", "size_bytes",
    ] + [f"cat_{c}" for c in cat_cols]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in all_stats:
            row = {k: s.get(k) for k in fields if not k.startswith("cat_")}
            for c in cat_cols:
                row[f"cat_{c}"] = s["category_tokens"].get(c, 0)
            w.writerow(row)


def write_turn_csv(all_stats: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "session_id", "turn_idx",
        "input_tokens", "cache_creation", "cache_read",
        "output_tokens", "new_input_total",
    ]
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in all_stats:
            for t in s["turns"]:
                row = {"session_id": s["session_id"], **t}
                w.writerow(row)


def print_report(all_stats: list[dict[str, Any]]) -> None:
    all_stats.sort(key=lambda s: s["api_cache_read"], reverse=True)
    print("=" * 100)
    print("Per-session summary (top 25 by cache_read)")
    print("=" * 100)
    print(f"{'session':<14} {'ver':<8} {'asst':>5} {'new_in':>10} {'cache_rd':>12} {'out':>8} "
          f"{'first_cc':>10} {'dhee_hits':>10} {'dhee_chars':>10}")
    print("-" * 100)
    for s in all_stats[:25]:
        new_in = s["api_input_tokens"] + s["api_cache_creation"]
        sid = s["session_id"][:12]
        ver = (s["version"] or "?")[:7]
        print(
            f"{sid:<14} {ver:<8} {s['turns_assistant']:>5} {new_in:>10,} "
            f"{s['api_cache_read']:>12,} {s['api_output_tokens']:>8,} "
            f"{(s['first_turn_cache_creation'] or 0):>10,} "
            f"{s['dhee_injection_hits']:>10} {s['dhee_injection_chars']:>10,}"
        )

    total_asst = sum(s["turns_assistant"] for s in all_stats)
    total_new_in = sum(s["api_input_tokens"] + s["api_cache_creation"] for s in all_stats)
    total_cr = sum(s["api_cache_read"] for s in all_stats)
    total_out = sum(s["api_output_tokens"] for s in all_stats)
    total_dhee_chars = sum(s["dhee_injection_chars"] for s in all_stats)
    total_dhee_hits = sum(s["dhee_injection_hits"] for s in all_stats)

    print("-" * 100)
    print(f"Aggregate: {total_asst:,} assistant turns across {len(all_stats)} sessions")
    print(f"  new input tokens    (paid full rate): {total_new_in:>14,}")
    print(f"  cache-read tokens   (paid 10% rate):  {total_cr:>14,}")
    print(f"  output tokens:                        {total_out:>14,}")
    if total_asst:
        print(f"  avg per turn: new_in={total_new_in/total_asst:,.0f}  "
              f"cache_read={total_cr/total_asst:,.0f}  out={total_out/total_asst:,.0f}")

    print(f"\nDhee injections detected: {total_dhee_hits:,} hits, "
          f"{total_dhee_chars:,} chars (~{token_estimate(str('x')*total_dhee_chars):,} tokens est.)")

    combined: dict[str, int] = defaultdict(int)
    combined_chars: dict[str, int] = defaultdict(int)
    for s in all_stats:
        for k, v in s["category_tokens"].items():
            combined[k] += v
        for k, v in s["category_chars"].items():
            combined_chars[k] += v

    total_cat = sum(combined.values()) or 1
    print("\nCategory breakdown (estimated from transcript content, ~3.5 chars/token):")
    print(f"{'category':<22} {'est_tokens':>14} {'pct':>6} {'chars':>14}")
    print("-" * 60)
    for cat, tok in sorted(combined.items(), key=lambda x: -x[1]):
        pct = 100.0 * tok / total_cat
        print(f"{cat:<22} {tok:>14,} {pct:>5.1f}% {combined_chars[cat]:>14,}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sessions-dir",
        default=str(Path.home() / ".claude" / "projects" / "-Users-chitranjanmalviya-Desktop-Dhee"),
    )
    parser.add_argument("--out-dir", default="runs/phase0")
    parser.add_argument("--min-turns", type=int, default=1,
                        help="Skip sessions with fewer assistant turns than this.")
    args = parser.parse_args()

    sess_dir = Path(args.sessions_dir).expanduser()
    if not sess_dir.exists():
        print(f"ERROR: sessions dir not found: {sess_dir}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    transcripts = sorted(sess_dir.glob("*.jsonl"))
    if not transcripts:
        print(f"ERROR: no .jsonl transcripts in {sess_dir}", file=sys.stderr)
        return 1

    all_stats: list[dict[str, Any]] = []
    for p in transcripts:
        try:
            s = analyze_session(p)
        except Exception as exc:
            print(f"  skipped {p.name}: {exc}", file=sys.stderr)
            continue
        if s["turns_assistant"] < args.min_turns:
            continue
        all_stats.append(s)

    if not all_stats:
        print("no sessions with assistant turns — nothing to report", file=sys.stderr)
        return 1

    print_report(all_stats)
    write_session_csv(all_stats, out_dir / "sessions.csv")
    write_turn_csv(all_stats, out_dir / "turns.csv")
    print(f"\nWrote: {out_dir/'sessions.csv'}, {out_dir/'turns.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
