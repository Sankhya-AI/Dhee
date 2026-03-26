"""Query intent classifier for retrieval routing.

Regex-based classifier (zero LLM cost, sub-millisecond) that determines
whether a query targets episodic memories (conversations, events),
semantic memories (facts, preferences), or is ambiguous (mixed).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import List, Tuple


class QueryIntent(str, Enum):
    EPISODIC = "episodic"   # "when did", "last time", "what happened", "ago"
    SEMANTIC = "semantic"   # "what is", "prefer", "tell me about", "favorite"
    MIXED = "mixed"         # ambiguous or both signals


# Patterns that signal episodic (event/time-based) queries
_EPISODIC_PATTERNS: List[Tuple[re.Pattern, float]] = [
    (re.compile(r"\bwhen did\b", re.I), 1.0),
    (re.compile(r"\blast time\b", re.I), 1.0),
    (re.compile(r"\bwhat happened\b", re.I), 1.0),
    (re.compile(r"\bdo you remember\b", re.I), 0.8),
    (re.compile(r"\brecall\b", re.I), 0.6),
    (re.compile(r"\b\d+\s*(days?|weeks?|months?|hours?)\s+ago\b", re.I), 1.0),
    (re.compile(r"\byesterday\b", re.I), 0.9),
    (re.compile(r"\blast (week|month|year|session|conversation)\b", re.I), 1.0),
    (re.compile(r"\bwe (discussed|talked|mentioned|said)\b", re.I), 0.9),
    (re.compile(r"\bi (said|told|mentioned|asked)\b", re.I), 0.8),
    (re.compile(r"\bwhat did (i|we|you)\b", re.I), 0.9),
    (re.compile(r"\bhistory of\b", re.I), 0.7),
    (re.compile(r"\btimeline\b", re.I), 0.7),
    (re.compile(r"\bsequence of events\b", re.I), 1.0),
    (re.compile(r"\bfirst time\b", re.I), 0.8),
    (re.compile(r"\bhow many times\b", re.I), 0.7),
]

# Patterns that signal semantic (fact/knowledge-based) queries
_SEMANTIC_PATTERNS: List[Tuple[re.Pattern, float]] = [
    (re.compile(r"\bwhat is\b", re.I), 0.8),
    (re.compile(r"\bwhat are\b", re.I), 0.7),
    (re.compile(r"\bwhat'?s my\b", re.I), 0.9),
    (re.compile(r"\bprefer\b", re.I), 0.9),
    (re.compile(r"\bfavorite\b", re.I), 0.9),
    (re.compile(r"\btell me about\b", re.I), 0.7),
    (re.compile(r"\bwho is\b", re.I), 0.7),
    (re.compile(r"\bexplain\b", re.I), 0.6),
    (re.compile(r"\bdescribe\b", re.I), 0.6),
    (re.compile(r"\bhow (do|does|to)\b", re.I), 0.7),
    (re.compile(r"\bprocess for\b", re.I), 0.8),
    (re.compile(r"\bsteps to\b", re.I), 0.7),
    (re.compile(r"\bprocedure\b", re.I), 0.7),
    (re.compile(r"\bworkflow\b", re.I), 0.7),
    (re.compile(r"\bdefault\b", re.I), 0.5),
    (re.compile(r"\busually\b", re.I), 0.6),
    (re.compile(r"\balways\b", re.I), 0.5),
    (re.compile(r"\bnever\b", re.I), 0.5),
]


def classify_intent(query: str) -> QueryIntent:
    """Classify a search query as episodic, semantic, or mixed.

    Returns QueryIntent enum based on regex pattern matching.
    Zero LLM cost, sub-millisecond execution.
    """
    if not query or not query.strip():
        return QueryIntent.MIXED

    episodic_score = 0.0
    semantic_score = 0.0

    for pattern, weight in _EPISODIC_PATTERNS:
        if pattern.search(query):
            episodic_score += weight

    for pattern, weight in _SEMANTIC_PATTERNS:
        if pattern.search(query):
            semantic_score += weight

    if episodic_score == 0.0 and semantic_score == 0.0:
        return QueryIntent.MIXED

    # Require clear dominance (>1.5x) to declare a specific intent
    if episodic_score > semantic_score * 1.5:
        return QueryIntent.EPISODIC
    if semantic_score > episodic_score * 1.5:
        return QueryIntent.SEMANTIC

    return QueryIntent.MIXED
