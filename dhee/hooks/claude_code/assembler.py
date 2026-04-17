"""Context assembler — selects what information enters each LLM call.

This is Dhee's value proposition: instead of the host agent dumping its
entire CLAUDE.md + AGENTS.md + skills into every prompt, Dhee selects
only the chunks relevant to THIS specific prompt and assembles them into
a token-budgeted injection.

The economics (revised 2026-04-17):
    Phase 0 audit (`dhee/benchmarks/phase0_context_audit.py`) parsed real
    Anthropic `usage` fields across 14 sessions / 4,680 assistant turns.
    CLAUDE.md first-turn cost bundled with system prompt + skill catalog
    + tool schemas is ~5–8K tokens — a rounding error, not the fat. The
    actual fat is **tool-result accumulation** (~57% of growing content)
    and tool-use inputs (~31%). Top 10% of turns account for 75% of new
    input.

    This assembler therefore does relevance-filtering on *docs* (a small
    marginal win), not the primary win. The primary router lever is
    `dhee.router` — digest-at-source MCP wrappers (`dhee_read`, etc.)
    that keep raw tool output out of the context entirely.

The assembler is a pure selection pipeline:
    query → vector search → filter(kind, score) → budget → render

No LLM call. Vector similarity + heading-path matching is enough for
structured markdown docs. A synthesis LLM call is a future optimization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CHARS_PER_TOKEN = 3.5


@dataclass
class DocMatch:
    """A doc-chunk that matched the current query."""
    text: str
    source_path: str
    heading_breadcrumb: str
    score: float
    chunk_index: int

    @property
    def source_name(self) -> str:
        """Short display name: 'CLAUDE.md' not the full path."""
        parts = self.source_path.rsplit("/", 1)
        return parts[-1] if parts else self.source_path


@dataclass
class AssembledContext:
    """The complete context package for one hook invocation."""
    doc_matches: list[DocMatch]
    typed_cognition: dict[str, Any]
    doc_tokens_used: int = 0
    cognition_tokens_used: int = 0

    @property
    def has_docs(self) -> bool:
        return bool(self.doc_matches)

    @property
    def has_cognition(self) -> bool:
        from dhee.hooks.claude_code.signal import has_cognition_signal
        return has_cognition_signal(self.typed_cognition)

    @property
    def is_empty(self) -> bool:
        return not self.has_docs and not self.has_cognition

    @property
    def total_tokens(self) -> int:
        return self.doc_tokens_used + self.cognition_tokens_used


def assemble(
    dhee: Any,
    query: str,
    *,
    doc_budget_tokens: int = 800,
    cognition_budget_tokens: int = 700,
    score_threshold: float = 0.55,
    max_doc_chunks: int = 5,
    include_cognition: bool = True,
) -> AssembledContext:
    """Select relevant context for a single prompt/task.

    Searches Dhee's vector store for doc_chunks matching ``query``,
    filters by score threshold, and budgets by token count. Optionally
    also pulls typed cognition (insights/beliefs/etc.) from the
    HyperContext.

    Returns an ``AssembledContext`` that the renderer can turn into XML.
    """
    # -- Phase 1: Doc chunk retrieval --
    doc_matches = _search_doc_chunks(
        dhee,
        query=query,
        score_threshold=score_threshold,
        max_chunks=max_doc_chunks,
        token_budget=doc_budget_tokens,
    )
    doc_tokens = sum(int(len(m.text) / CHARS_PER_TOKEN) for m in doc_matches)

    # -- Phase 2: Typed cognition (optional) --
    typed = {}
    if include_cognition:
        try:
            import os
            ctx = dhee.context(
                task_description=query or None,
                user_id=os.environ.get("DHEE_USER_ID", "default"),
            )
            if isinstance(ctx, dict):
                typed = _strip_internal_warnings(ctx)
        except Exception:
            pass

    return AssembledContext(
        doc_matches=doc_matches,
        typed_cognition=typed,
        doc_tokens_used=doc_tokens,
    )


# Degradation messages come from internal error paths (e.g. embedder 401s,
# store read failures). They're meant for logs and telemetry, not for the
# LLM — surfacing them wastes tokens and can leak error-string content
# (headers, stack traces, model names). Stripped here so the renderer
# never sees them.
_INTERNAL_WARNING_PREFIXES = (
    "Context assembly degraded:",
    "Cognitive state degraded:",
)


def _strip_internal_warnings(ctx: dict[str, Any]) -> dict[str, Any]:
    warnings = ctx.get("warnings")
    if not isinstance(warnings, list):
        return ctx
    visible = [
        w for w in warnings
        if isinstance(w, str) and not w.startswith(_INTERNAL_WARNING_PREFIXES)
    ]
    if len(visible) == len(warnings):
        return ctx
    scrubbed = dict(ctx)
    scrubbed["warnings"] = visible
    return scrubbed


def assemble_docs_only(
    dhee: Any,
    query: str,
    *,
    token_budget: int = 500,
    score_threshold: float = 0.55,
    max_chunks: int = 3,
) -> list[DocMatch]:
    """Lightweight doc-only retrieval for per-turn injection.

    Used by UserPromptSubmit where we don't want the full HyperContext
    overhead — just the doc chunks that match this specific prompt.

    Applies a relative-score gate: the top match must score at least
    ``top_score_min`` above the noise floor. When all doc chunks score
    similarly (as happens with off-topic queries like "explain dark
    matter"), none represent genuine topical relevance — they're just
    the embedding model's nearest-but-still-wrong neighbors.
    """
    matches = _search_doc_chunks(
        dhee,
        query=query,
        score_threshold=score_threshold,
        max_chunks=max_chunks,
        token_budget=token_budget,
    )
    if not matches:
        return []
    # Gate: top match must beat threshold by a meaningful margin.
    # An off-topic query returns all chunks in a tight 0.55-0.62 band.
    # A genuinely relevant query puts the top chunk at 0.70+ with
    # clear separation from the rest.
    if matches[0].score < 0.62:
        return []
    return matches


def _search_doc_chunks(
    dhee: Any,
    query: str,
    *,
    score_threshold: float,
    max_chunks: int,
    token_budget: int,
) -> list[DocMatch]:
    """Search the vector store for doc_chunk memories matching ``query``."""
    if not query or not query.strip():
        return []

    try:
        raw_results = dhee._engram.search(query, limit=30)
    except Exception:
        return []

    if not raw_results:
        return []

    # Filter to doc_chunks only.
    doc_results: list[tuple[float, dict]] = []
    for r in raw_results:
        meta = r.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        if meta.get("kind") != "doc_chunk":
            continue
        score = float(r.get("composite_score", r.get("score", 0.0)))
        if score < score_threshold:
            continue
        doc_results.append((score, r))

    if not doc_results:
        return []

    # Sort by score descending, take top-K.
    doc_results.sort(key=lambda x: x[0], reverse=True)

    budget_chars = int(token_budget * CHARS_PER_TOKEN)
    used = 0
    matches: list[DocMatch] = []

    for score, r in doc_results:
        if len(matches) >= max_chunks:
            break
        meta = r.get("metadata", {})
        text = str(r.get("memory", r.get("content", "")))
        cost = len(text)
        if used + cost > budget_chars:
            continue
        matches.append(DocMatch(
            text=text,
            source_path=str(meta.get("source_path", "")),
            heading_breadcrumb=str(meta.get("heading_breadcrumb", "")),
            score=round(score, 3),
            chunk_index=int(meta.get("chunk_index", 0)),
        ))
        used += cost

    return matches
