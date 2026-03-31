"""Retrieval helper utilities for search and evidence building.

Extracted from memory/main.py — stateless functions for echo boost,
reranking passages, evidence text building, temporal boosting, and
bitemporal metadata handling.
"""

from __future__ import annotations

import math
import re
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex patterns (compiled once)
# ---------------------------------------------------------------------------

_TEMPORAL_RECENT_QUERY_RE = re.compile(
    r"\b(recent(?:ly)?|latest|last\s+(?:week|month|day|few|couple)|today|yesterday"
    r"|this\s+(?:week|month)|past\s+\d+\s+(?:day|week|month)s?)\b",
    re.IGNORECASE,
)
_TEMPORAL_RANGE_QUERY_RE = re.compile(
    r"\b(since|between|from\s+\d{4}|after\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"|before\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))\b",
    re.IGNORECASE,
)
_TEMPORAL_TRANSACTIONAL_QUERY_RE = re.compile(
    r"\b(bought|purchased|ordered|paid|spent|received|shipped|delivered|charged)\b",
    re.IGNORECASE,
)


# Stop words to exclude from echo boost term matching
ECHO_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "has", "have", "had", "i", "me", "my", "we",
    "our", "you", "your", "he", "she", "it", "they", "them", "their",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "am", "will", "would", "shall", "should", "can", "could", "may",
    "might", "must", "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "about", "as", "into", "through", "during", "before", "after",
    "and", "but", "or", "nor", "not", "so", "if", "then", "than", "too",
    "very", "just", "how", "when", "where", "why", "all", "each", "some",
    "any", "no", "yes",
})


# ---------------------------------------------------------------------------
# Bitemporal helpers
# ---------------------------------------------------------------------------

def normalize_bitemporal_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def parse_bitemporal_datetime(value: Any) -> Optional[datetime]:
    normalized = normalize_bitemporal_value(value)
    if not normalized:
        return None
    text = normalized
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        date_match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
        if not date_match:
            return None
        try:
            d = date.fromisoformat(date_match.group(1))
        except ValueError:
            return None
        dt = datetime.combine(d, datetime.min.time())

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def attach_bitemporal_metadata(
    metadata: Optional[Dict[str, Any]],
    observed_time: str,
) -> Dict[str, Any]:
    md = dict(metadata or {})
    observed_norm = normalize_bitemporal_value(md.get("observed_time")) or observed_time
    md["observed_time"] = observed_norm

    event_candidate = (
        md.get("event_time")
        or md.get("session_date")
        or md.get("event_date")
        or md.get("timestamp")
        or md.get("date")
    )
    event_norm = normalize_bitemporal_value(event_candidate)
    if event_norm:
        md["event_time"] = event_norm
    return md


# ---------------------------------------------------------------------------
# Temporal boosting
# ---------------------------------------------------------------------------

def query_prefers_recency(query: str) -> bool:
    q = str(query or "")
    return bool(_TEMPORAL_RECENT_QUERY_RE.search(q) or _TEMPORAL_RANGE_QUERY_RE.search(q))


def query_is_transactional(query: str) -> bool:
    return bool(_TEMPORAL_TRANSACTIONAL_QUERY_RE.search(str(query or "")))


def compute_temporal_boost(
    *,
    query: str,
    metadata: Dict[str, Any],
    query_intent=None,
) -> float:
    if not metadata:
        return 0.0
    # Import here to avoid circular dependency
    try:
        from dhee.core.intent import QueryIntent
        is_episodic = query_intent == QueryIntent.EPISODIC
    except ImportError:
        is_episodic = False

    if not query_prefers_recency(query) and not is_episodic:
        return 0.0

    event_time = metadata.get("event_time") or metadata.get("session_date")
    event_dt = parse_bitemporal_datetime(event_time)
    if event_dt is None:
        return 0.0

    now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - event_dt).total_seconds() / 86400.0)

    decay_days = 30.0 if query_is_transactional(query) else 180.0
    recency = math.exp(-age_days / decay_days)
    boost = 0.20 * recency

    if _TEMPORAL_RANGE_QUERY_RE.search(str(query or "")) and age_days > 45.0:
        penalty = min(0.20, (age_days - 45.0) / 365.0)
        boost -= penalty

    return max(-0.25, min(0.25, boost))


# ---------------------------------------------------------------------------
# Echo boost
# ---------------------------------------------------------------------------

def calculate_echo_boost(
    query_lower: str,
    query_terms: set,
    metadata: Dict[str, Any],
) -> float:
    """Calculate re-ranking boost based on echo metadata matches."""
    boost = 0.0
    content_query_terms = query_terms - ECHO_STOP_WORDS

    # Keyword match boost
    keywords = metadata.get("echo_keywords", [])
    if keywords:
        keyword_matches = 0
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower in query_lower:
                keyword_matches += 1
            elif content_query_terms and any(
                term in kw_lower or kw_lower in term
                for term in content_query_terms
                if len(term) > 3
            ):
                keyword_matches += 1
        boost += keyword_matches * 0.06
        if content_query_terms and keyword_matches > 0:
            coverage = keyword_matches / len(content_query_terms)
            boost += coverage * 0.15

    # Question form similarity boost
    question_form = metadata.get("echo_question_form", "")
    if question_form and content_query_terms:
        q_terms = set(question_form.lower().split()) - ECHO_STOP_WORDS
        overlap = len(content_query_terms & q_terms)
        if overlap > 0:
            boost += min(0.15, overlap * 0.05)

    # Implication match boost
    implications = metadata.get("echo_implications", [])
    if implications and content_query_terms:
        for impl in implications:
            impl_terms = set(impl.lower().split()) - ECHO_STOP_WORDS
            if content_query_terms & impl_terms:
                boost += 0.03

    return min(0.3, boost)


# ---------------------------------------------------------------------------
# Text truncation and overlap helpers
# ---------------------------------------------------------------------------

def truncate_rerank_text(text: str, max_chars: int) -> str:
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        limit = 3500
    limit = max(1, limit)
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip()


def term_overlap_count(text: str, terms: set) -> int:
    if not terms:
        return 0
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


# ---------------------------------------------------------------------------
# Evidence and rerank passage builders
# ---------------------------------------------------------------------------

def build_rerank_snippet(
    *,
    memory_text: str,
    query_terms: set,
    max_chars: int,
    context_lines: int,
) -> str:
    normalized_text = str(memory_text or "")
    if not normalized_text.strip():
        return ""

    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    if not lines:
        return truncate_rerank_text(normalized_text, max_chars)

    header_prefixes = ("session date:", "user transcript:")
    selected_indices = set()
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if lowered.startswith(header_prefixes):
            selected_indices.add(idx)

    content_terms = {
        str(term).lower()
        for term in query_terms
        if isinstance(term, str) and len(term) > 3 and str(term).lower() not in ECHO_STOP_WORDS
    }

    effective_context = max(context_lines, 3)

    hit_found = False
    if content_terms:
        for idx, line in enumerate(lines):
            lowered = line.lower()
            if any(term in lowered for term in content_terms):
                hit_found = True
                start = max(0, idx - effective_context)
                end = min(len(lines), idx + effective_context + 1)
                selected_indices.update(range(start, end))

    if not hit_found:
        if len(lines) <= 30:
            selected_indices.update(range(len(lines)))
        else:
            selected_indices.update(range(0, min(len(lines), 15)))
            mid = len(lines) // 2
            mid_start = max(0, mid - 5)
            mid_end = min(len(lines), mid + 5)
            selected_indices.update(range(mid_start, mid_end))
            tail_start = max(0, len(lines) - 10)
            selected_indices.update(range(tail_start, len(lines)))

    ordered_lines = [lines[idx] for idx in sorted(selected_indices)]
    snippet = "\n".join(ordered_lines)
    return truncate_rerank_text(snippet, max_chars)
