"""Phase 8 — self-evolution tuner.

Analyzes ptr-store meta + expansion logs to compute per-(tool, intent)
expansion rate. Proposes and optionally applies depth changes:

    expansion_rate > 0.30   → deepen one step   (normal → deep, shallow → normal)
    expansion_rate < 0.05 & samples ≥ MIN → shallower one step (deep → normal, normal → shallow)
    else                    → no change

Thresholds are conservative on purpose: the cost of a too-shallow digest
is a re-expand round-trip; the cost of a too-deep digest is extra
context tokens. Asymmetric, but not by much. We want small deliberate
steps, not oscillation.

Run modes:

    suggest  (default) — print per-bucket table, propose changes, exit
    apply              — write suggested changes to policy file
    clear              — reset all tuned entries

Offline by design. No autonomous in-handler retune.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dhee.router import policy as _policy
from dhee.router import ptr_store as _ptr

MIN_SAMPLES_FOR_SHALLOWER = 10
HIGH_EXPANSION = 0.30
LOW_EXPANSION = 0.05

_DEPTH_ORDER = ("shallow", "normal", "deep")


@dataclass
class Bucket:
    tool: str
    intent: str
    calls: int = 0
    expansions: int = 0
    current_depth: str = "normal"

    @property
    def rate(self) -> float:
        return (self.expansions / self.calls) if self.calls else 0.0


@dataclass
class Suggestion:
    bucket: Bucket
    new_depth: str
    reason: str


@dataclass
class TuneReport:
    buckets: list[Bucket] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "buckets": [
                {
                    "tool": b.tool,
                    "intent": b.intent,
                    "calls": b.calls,
                    "expansions": b.expansions,
                    "expansion_rate": round(b.rate, 4),
                    "current_depth": b.current_depth,
                }
                for b in self.buckets
            ],
            "suggestions": [
                {
                    "tool": s.bucket.tool,
                    "intent": s.bucket.intent,
                    "from": s.bucket.current_depth,
                    "to": s.new_depth,
                    "reason": s.reason,
                }
                for s in self.suggestions
            ],
        }


def _iter_session_dirs() -> list[Path]:
    root = _ptr._root()
    if not root.exists():
        return []
    return [d for d in root.iterdir() if d.is_dir()]


def _collect_buckets() -> dict[tuple[str, str], Bucket]:
    buckets: dict[tuple[str, str], Bucket] = {}

    for sdir in _iter_session_dirs():
        # Meta files → call counts per (tool, intent).
        for mf in sdir.glob("*.json"):
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            tool = str(meta.get("tool") or "")
            if not tool:
                continue
            intent = str(
                meta.get("intent")
                or meta.get("class")
                or meta.get("kind")
                or "other"
            )
            key = (tool, intent)
            b = buckets.setdefault(key, Bucket(tool=tool, intent=intent))
            b.calls += 1

        # Expansion log → expansion counts per (tool, intent). Older
        # records predate attribution — skip when we can't resolve.
        log = sdir / "expansions.jsonl"
        if not log.exists():
            continue
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
                    tool = str(rec.get("tool") or "")
                    intent = str(rec.get("intent") or "")
                    if not tool or not intent:
                        continue
                    key = (tool, intent)
                    b = buckets.setdefault(key, Bucket(tool=tool, intent=intent))
                    b.expansions += 1
        except Exception:
            continue

    # Overlay current policy depths.
    pol = _policy.load().get("depths", {})
    for (tool, intent), b in buckets.items():
        depth = pol.get(tool, {}).get(intent) if isinstance(pol.get(tool), dict) else None
        if depth in _DEPTH_ORDER:
            b.current_depth = depth

    return buckets


def _step(depth: str, direction: int) -> str | None:
    try:
        i = _DEPTH_ORDER.index(depth)
    except ValueError:
        i = _DEPTH_ORDER.index("normal")
    j = i + direction
    if j < 0 or j >= len(_DEPTH_ORDER):
        return None
    return _DEPTH_ORDER[j]


def _suggest(b: Bucket) -> Suggestion | None:
    if b.calls == 0:
        return None
    if b.rate > HIGH_EXPANSION:
        new = _step(b.current_depth, +1)
        if new is None:
            return None
        return Suggestion(
            bucket=b,
            new_depth=new,
            reason=f"expansion_rate={b.rate:.1%} > {HIGH_EXPANSION:.0%} — digests too shallow",
        )
    if b.rate < LOW_EXPANSION and b.calls >= MIN_SAMPLES_FOR_SHALLOWER:
        new = _step(b.current_depth, -1)
        if new is None:
            return None
        return Suggestion(
            bucket=b,
            new_depth=new,
            reason=f"expansion_rate={b.rate:.1%} < {LOW_EXPANSION:.0%} (n={b.calls}) — safe to shrink",
        )
    return None


def build_report() -> TuneReport:
    buckets = list(_collect_buckets().values())
    buckets.sort(key=lambda b: (-b.calls, b.tool, b.intent))
    suggestions: list[Suggestion] = []
    for b in buckets:
        s = _suggest(b)
        if s is not None:
            suggestions.append(s)
    return TuneReport(buckets=buckets, suggestions=suggestions)


def apply(report: TuneReport) -> int:
    """Persist every suggestion to policy. Returns count applied."""
    count = 0
    for s in report.suggestions:
        try:
            _policy.set_depth(s.bucket.tool, s.bucket.intent, s.new_depth)
            count += 1
        except Exception:
            continue
    return count


def format_human(report: TuneReport) -> str:
    if not report.buckets:
        return (
            "  No router activity yet. Tune needs ptr-store meta and "
            "expansion logs to compute per-bucket rates."
        )

    lines = [
        f"  {'tool':<8} {'intent':<14} {'calls':>6} {'exp':>5} {'rate':>7} {'depth':<8}",
    ]
    for b in report.buckets:
        lines.append(
            f"  {b.tool:<8} {b.intent:<14} {b.calls:>6} {b.expansions:>5} "
            f"{b.rate*100:>6.1f}% {b.current_depth:<8}"
        )
    lines.append("")
    if not report.suggestions:
        lines.append("  No tune suggestions — rates are within policy bands.")
        return "\n".join(lines)

    lines.append("  Suggestions (run `dhee router tune apply` to persist):")
    for s in report.suggestions:
        lines.append(
            f"  • {s.bucket.tool}/{s.bucket.intent}: "
            f"{s.bucket.current_depth} → {s.new_depth}"
        )
        lines.append(f"      {s.reason}")
    return "\n".join(lines)
