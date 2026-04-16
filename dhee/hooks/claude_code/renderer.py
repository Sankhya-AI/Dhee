"""Render Dhee HyperContext as token-budgeted XML for Claude Code.

Takes the raw dict from ``Dhee.context()`` and produces a compact XML block
that fits inside a token budget. No Pydantic models, no sankhya-os dependency.

Priority order (highest first):
  session > performance > insights > intentions > memories >
  beliefs > policies > episodes > warnings

Sections that would overflow the budget are dropped silently, lowest priority
first. Empty sections never emit tags.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape as _xml_escape

DEFAULT_TOKEN_BUDGET = 1500
CHARS_PER_TOKEN = 3.5

HEADER = (
    "Dhee cognition active. The context block below contains your memory and "
    "learned patterns from prior sessions. Treat as ground truth for this turn — "
    "do not re-derive what is already here. Honor warnings literally."
)


def render_context(
    ctx: dict[str, Any],
    *,
    task_description: str | None = None,
    max_tokens: int = DEFAULT_TOKEN_BUDGET,
    max_memories: int = 8,
    max_insights: int = 5,
    max_intentions: int = 3,
) -> str:
    """Render a Dhee context dict as XML for Claude Code system-prompt injection."""
    sections: list[tuple[int, list[str]]] = [
        (100, _session_block(ctx.get("last_session"))),
        (90, _performance_block(ctx.get("performance", []))),
        (80, _insights_block(ctx.get("insights", []), max_insights)),
        (75, _intentions_block(ctx.get("intentions", []), max_intentions)),
        (70, _memories_block(ctx.get("memories", []), max_memories)),
        (60, _beliefs_block(ctx.get("beliefs", []))),
        (50, _policies_block(ctx.get("policies", []))),
        (40, _episodes_block(ctx.get("episodes", []))),
        (30, _warnings_block(ctx.get("warnings", []))),
    ]

    budget_chars = int(max_tokens * CHARS_PER_TOKEN)

    attrs = ""
    if task_description:
        attrs = f' task="{_esc_attr(task_description[:120])}"'

    open_tag = f"<dhee-context{attrs}>"
    close_tag = "</dhee-context>"
    body: list[str] = [HEADER, open_tag]
    used = len(HEADER) + len(open_tag) + len(close_tag) + 2  # newlines

    for _priority, lines in sections:
        if not lines:
            continue
        block = "\n".join(f"  {line}" for line in lines)
        cost = len(block) + 1
        if used + cost > budget_chars:
            continue
        body.append(block)
        used += cost

    body.append(close_tag)
    return "\n".join(body) + "\n"


def estimate_tokens(text: str) -> int:
    """Conservative token estimate."""
    return int(len(text) / CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Section builders — each returns list[str] of XML lines, [] if nothing to emit
# ---------------------------------------------------------------------------


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
    attrs = _attrs(status=status) if status else ""
    inner: list[str] = []
    if summary:
        inner.append(f"<summary>{_esc(summary[:200])}</summary>")
    if decisions:
        inner.append(_flat_list("decisions", "d", decisions))
    if files:
        inner.append(_flat_list("files", "f", files))
    if todos:
        inner.append(_flat_list("todos", "t", todos))
    return _container("session", attrs, inner)


def _performance_block(perf: list[Any]) -> list[str]:
    if not perf:
        return []
    out: list[str] = ["<performance>"]
    for row in perf[:5]:
        if not isinstance(row, dict):
            continue
        attrs = _attrs(
            type=str(row.get("task_type", "")),
            attempts=str(row.get("total_attempts", 0)),
            best=_fmt(row.get("best_score")),
            avg=_fmt(row.get("avg_score")),
            trend=_fmt(row.get("trend")),
        )
        if attrs:
            out.append(f"  <row {attrs}/>")
    if len(out) == 1:
        return []
    out.append("</performance>")
    return out


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
            a = _attrs(tag=tag) if tag else ""
            items.append(_tag("i", a, content))
        elif isinstance(row, str) and row:
            items.append(_tag("i", "", row))
    if not items:
        return []
    return ["<insights>"] + [f"  {i}" for i in items] + ["</insights>"]


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
            a = _attrs(triggers=trig) if trig else ""
            items.append(_tag("i", a, content))
        elif isinstance(row, str) and row:
            items.append(_tag("i", "", row))
    if not items:
        return []
    return ["<intentions>"] + [f"  {i}" for i in items] + ["</intentions>"]


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
            items.append(f'<m s="{score:.2f}">{_esc(text)}</m>')
        elif isinstance(m, str) and m:
            items.append(f"<m>{_esc(m)}</m>")
    if not items:
        return []
    return ["<memories>"] + [f"  {i}" for i in items] + ["</memories>"]


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
        items.append(_tag("b", _attrs(type=btype, conf=conf), claim))
    if not items:
        return []
    return ["<beliefs>"] + [f"  {i}" for i in items] + ["</beliefs>"]


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
    if not items:
        return []
    return ["<policies>"] + [f"  {i}" for i in items] + ["</policies>"]


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
        items.append(_tag("e", _attrs(type=etype), summary))
    if not items:
        return []
    return ["<episodes>"] + [f"  {i}" for i in items] + ["</episodes>"]


def _warnings_block(warnings: list[str]) -> list[str]:
    if not warnings:
        return []
    inner = [f"<w>{_esc(str(w))}</w>" for w in warnings if w]
    if not inner:
        return []
    return _container("warnings", "", inner)


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


def _tag(name: str, attrs: str, text: str) -> str:
    safe = _esc(text)
    if attrs:
        return f"<{name} {attrs}>{safe}</{name}>"
    return f"<{name}>{safe}</{name}>"


def _flat_list(wrapper: str, item: str, items: list) -> str:
    if not items:
        return ""
    inner = " ".join(f"<{item}>{_esc(str(x))}</{item}>" for x in items if x)
    return f"<{wrapper}>{inner}</{wrapper}>"


def _container(tag: str, attrs: str, inner: list[str]) -> list[str]:
    inner = [line for line in inner if line]
    if not inner:
        return []
    open_tag = f"<{tag} {attrs}>" if attrs else f"<{tag}>"
    return [open_tag, *[f"  {line}" for line in inner], f"</{tag}>"]
