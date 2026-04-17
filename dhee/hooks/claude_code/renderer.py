"""Render Dhee context as token-budgeted XML for Claude Code.

Priority: docs > session > performance > insights > intentions >
beliefs > policies > memories > episodes > warnings.

Empty input → empty string (no tags).

Token philosophy (Caveman-inspired): drop structural fluff, keep
technical substance exact. No indentation, short tags, no wrapper
nesting, no redundant metadata. Every byte earns its place.

Phase 5 (2026-04-17): root is ``<dhee v="1">`` with typed
sections. Doc chunks carry ``src``/``head``/``s`` so the model knows
the origin, not just a weighted fragment. ``<edits>`` is a first-class
section, fed in from the edit ledger. Schema stays terse — JSON-heavy
envelopes were rejected as more expensive than the caveman tags.
"""

from __future__ import annotations

import os
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

DEFAULT_TOKEN_BUDGET = 1500
CHARS_PER_TOKEN = 3.5


def render_context(
    ctx: dict[str, Any],
    *,
    task_description: str | None = None,
    max_tokens: int = DEFAULT_TOKEN_BUDGET,
    max_memories: int = 8,
    max_insights: int = 5,
    max_intentions: int = 3,
    doc_matches: list | None = None,
    edits_block: str | None = None,
) -> str:
    """Render Dhee context dict as flat XML for Claude Code injection.

    Returns empty string when nothing to inject.
    """
    sections: list[tuple[int, list[str]]] = [
        (120, _router_block()),
        (115, _edits_section(edits_block)),
        (110, _docs_block(doc_matches)),
        (100, _session_block(ctx.get("last_session"))),
        (90, _performance_block(ctx.get("performance", []))),
        (80, _insights_block(ctx.get("insights", []), max_insights)),
        (75, _intentions_block(ctx.get("intentions", []), max_intentions)),
        (65, _beliefs_block(ctx.get("beliefs", []))),
        (55, _policies_block(ctx.get("policies", []))),
        (45, _memories_block(ctx.get("memories", []), max_memories)),
        (40, _episodes_block(ctx.get("episodes", []))),
        (30, _warnings_block(ctx.get("warnings", []))),
    ]

    non_empty = [(p, lines) for p, lines in sections if lines]
    if not non_empty:
        return ""

    budget_chars = int(max_tokens * CHARS_PER_TOKEN)

    attrs = ""
    if task_description:
        attrs = f' task="{_esc_attr(task_description[:120])}"'
    attrs += ' v="1"'

    open_tag = f"<dhee{attrs}>"
    close_tag = "</dhee>"
    body: list[str] = [open_tag]
    used = len(open_tag) + len(close_tag) + 1

    included_any = False
    for _priority, lines in non_empty:
        block = "\n".join(lines)
        cost = len(block) + 1
        if used + cost > budget_chars:
            continue
        body.append(block)
        used += cost
        included_any = True

    if not included_any:
        return ""

    body.append(close_tag)
    return "\n".join(body) + "\n"


def estimate_tokens(text: str) -> int:
    """Conservative token estimate."""
    return int(len(text) / CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Section builders — each returns list[str], [] if nothing
# ---------------------------------------------------------------------------


_ROUTER_NUDGE = (
    "router=on. Prefer mcp__dhee__dhee_read over Read for files >200 lines "
    "(pass offset/limit for ranges). Prefer mcp__dhee__dhee_bash over Bash "
    "for commands likely >2KB output (git log/diff, pytest, find, grep, "
    "ls -R). After a subagent/large tool return, pass the text through "
    "mcp__dhee__dhee_agent to keep raw out of context. Call "
    "mcp__dhee__dhee_expand_result(ptr) only when a digest is genuinely "
    "insufficient — raw re-enters context."
)


def _router_block() -> list[str]:
    """One-time router nudge. Only rendered when DHEE_ROUTER=1."""
    if os.environ.get("DHEE_ROUTER") != "1":
        return []
    return [f"<router>{_xml_escape(_ROUTER_NUDGE)}</router>"]


def _docs_block(doc_matches: list | None) -> list[str]:
    if not doc_matches:
        return []
    items: list[str] = []
    for m in doc_matches:
        head = getattr(m, "heading_breadcrumb", "") or ""
        src = getattr(m, "source_name", "") or ""
        score = getattr(m, "score", 0.0)
        text = getattr(m, "text", "")
        if not text:
            continue
        if head and text.startswith(head):
            text = text[len(head):].lstrip("\n")
        attrs = _attrs(src=src, head=head)
        score_attr = _score_attr(score)
        a = f"{attrs} {score_attr}" if attrs else score_attr
        items.append(_tag("doc", a, text))
    return items


def _edits_section(edits_block: str | None) -> list[str]:
    if not edits_block:
        return []
    return [edits_block]


def _session_block(session: dict[str, Any] | None) -> list[str]:
    if not session or not isinstance(session, dict):
        return []
    decisions = session.get("decisions") or []
    files = session.get("files_touched") or session.get("files") or []
    todos = session.get("todos") or session.get("todos_remaining") or []
    summary = session.get("summary") or session.get("task_summary") or ""
    status = session.get("status", "")
    if not (decisions or files or todos or summary):
        return []
    parts: list[str] = []
    if summary:
        parts.append(_esc(summary[:200]))
    if decisions:
        parts.append("decisions:" + ",".join(_esc(str(d)) for d in decisions))
    if files:
        parts.append("files:" + ",".join(_esc(str(f)) for f in files))
    if todos:
        parts.append("todo:" + ",".join(_esc(str(t)) for t in todos))
    a = f' st="{_esc_attr(status)}"' if status else ""
    return [f"<session{a}>{' | '.join(parts)}</session>"]


def _performance_block(perf: list[Any]) -> list[str]:
    if not perf:
        return []
    items: list[str] = []
    for row in perf[:5]:
        if not isinstance(row, dict):
            continue
        attrs = _attrs(
            type=str(row.get("task_type", "")),
            n=str(row.get("total_attempts", 0)),
            best=_fmt(row.get("best_score")),
            avg=_fmt(row.get("avg_score")),
            trend=_fmt(row.get("trend")),
        )
        if attrs:
            items.append(f"<perf {attrs}/>")
    return items


def _insights_block(insights: list[Any], limit: int) -> list[str]:
    if not insights:
        return []
    items: list[str] = []
    for row in insights[:limit]:
        if isinstance(row, dict):
            content = str(row.get("content", ""))
            tag = str(row.get("task_type", row.get("tag", "")))
            if not content:
                continue
            a = f' tag="{_esc_attr(tag)}"' if tag else ""
            items.append(f"<i{a}>{_esc(content)}</i>")
        elif isinstance(row, str) and row:
            items.append(f"<i>{_esc(row)}</i>")
    return items


def _intentions_block(intentions: list[Any], limit: int) -> list[str]:
    if not intentions:
        return []
    items: list[str] = []
    for row in intentions[:limit]:
        if isinstance(row, dict):
            content = str(
                row.get("content")
                or row.get("remember_to")
                or row.get("description")
                or ""
            )
            triggers = row.get("trigger_keywords") or row.get("triggers") or []
            if not content:
                continue
            trig = ",".join(str(t) for t in triggers[:5]) if isinstance(triggers, list) else str(triggers)
            a = f' triggers="{_esc_attr(trig)}"' if trig else ""
            items.append(f"<intent{a}>{_esc(content)}</intent>")
        elif isinstance(row, str) and row:
            items.append(f"<intent>{_esc(row)}</intent>")
    return items


def _memories_block(memories: list[Any], limit: int) -> list[str]:
    if not memories:
        return []

    def _score(m: Any) -> float:
        if isinstance(m, dict):
            return float(m.get("score", m.get("composite_score", m.get("strength", 0))))
        return 0.0

    ranked = sorted(memories, key=_score, reverse=True)[:limit]
    items: list[str] = []
    for m in ranked:
        if isinstance(m, dict):
            text = str(m.get("memory", m.get("content", m.get("details", ""))))
            score = _score(m)
            if not text:
                continue
            items.append(f"<m {_score_attr(score)}>{_esc(text)}</m>")
        elif isinstance(m, str) and m:
            items.append(f"<m>{_esc(m)}</m>")
    return items


def _beliefs_block(beliefs: list[Any]) -> list[str]:
    if not beliefs:
        return []
    items: list[str] = []
    for b in beliefs[:5]:
        if not isinstance(b, dict):
            continue
        claim = str(b.get("claim", b.get("content", "")))
        btype = str(b.get("belief_type", b.get("type", "")))
        conf = _fmt(b.get("confidence"))
        if not claim:
            continue
        a = _attrs(type=btype, conf=conf)
        items.append(_tag("b", a, claim))
    return items


def _policies_block(policies: list[Any]) -> list[str]:
    if not policies:
        return []
    items: list[str] = []
    for p in policies[:3]:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", ""))
        desc = str(p.get("description", ""))
        conf = _fmt(p.get("confidence"))
        if not (name or desc):
            continue
        items.append(_tag("p", _attrs(name=name, conf=conf), desc))
    return items


def _episodes_block(episodes: list[Any]) -> list[str]:
    if not episodes:
        return []
    items: list[str] = []
    for e in episodes[:3]:
        if not isinstance(e, dict):
            continue
        summary = str(e.get("summary", ""))
        etype = str(e.get("episode_type", e.get("type", "")))
        if not summary:
            continue
        a = f' type="{_esc_attr(etype)}"' if etype else ""
        items.append(f"<e{a}>{_esc(summary)}</e>")
    return items


def _warnings_block(warnings: list[str]) -> list[str]:
    if not warnings:
        return []
    return [f"<w>{_esc(str(w))}</w>" for w in warnings if w]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    return _xml_escape(str(text))


def _esc_attr(text: str) -> str:
    return _xml_escape(str(text), {'"': "&quot;"})


def _attrs(**kwargs: str) -> str:
    parts: list[str] = []
    for key, value in kwargs.items():
        if value is None or value == "":
            continue
        parts.append(f'{key}="{_esc_attr(value)}"')
    return " ".join(parts)


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def _score_attr(score: float) -> str:
    return f's="{score:.2f}"'


def _tag(name: str, attrs: str, text: str) -> str:
    safe = _esc(text)
    if attrs:
        return f"<{name} {attrs}>{safe}</{name}>"
    return f"<{name}>{safe}</{name}>"
