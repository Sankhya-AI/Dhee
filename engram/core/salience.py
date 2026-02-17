"""Salience tagging for memories.

Computes emotional valence, arousal, and overall salience score.
High-salience memories decay slower and rank higher in search.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Heuristic keyword lists for fast salience estimation
_POSITIVE_WORDS = frozenset({
    "love", "great", "excellent", "amazing", "wonderful", "happy", "success",
    "perfect", "awesome", "fantastic", "brilliant", "enjoy", "solved", "fixed",
    "win", "achieved", "celebrate", "milestone", "breakthrough", "promoted",
})
_NEGATIVE_WORDS = frozenset({
    "hate", "terrible", "awful", "horrible", "fail", "failure", "crash",
    "error", "bug", "broken", "angry", "frustrated", "blocked", "lost",
    "dead", "killed", "disaster", "critical", "urgent", "emergency",
})
_HIGH_AROUSAL_WORDS = frozenset({
    "urgent", "critical", "emergency", "asap", "immediately", "deadline",
    "panic", "crash", "outage", "breaking", "alert", "important", "warning",
    "danger", "production", "incident", "blocker", "showstopper",
})

_SALIENCE_PROMPT = """Rate the emotional content of this text.

Text: {content}

Respond in JSON:
{{"valence": 0.0, "arousal": 0.0, "reasoning": "..."}}

valence: -1.0 (very negative) to 1.0 (very positive), 0.0 = neutral
arousal: 0.0 (calm/routine) to 1.0 (intense/urgent)"""


def compute_salience_heuristic(content: str) -> Dict[str, float]:
    """Fast heuristic salience computation from keyword matching."""
    words = set(re.findall(r'\b\w+\b', content.lower()))

    pos_count = len(words & _POSITIVE_WORDS)
    neg_count = len(words & _NEGATIVE_WORDS)
    arousal_count = len(words & _HIGH_AROUSAL_WORDS)

    total_emotional = pos_count + neg_count
    if total_emotional > 0:
        valence = (pos_count - neg_count) / total_emotional
    else:
        valence = 0.0

    arousal = min(1.0, arousal_count * 0.25)

    salience_score = min(1.0, (abs(valence) + arousal) / 2)

    return {
        "sal_valence": round(valence, 3),
        "sal_arousal": round(arousal, 3),
        "sal_salience_score": round(salience_score, 3),
    }


def compute_salience_llm(content: str, llm: Any) -> Dict[str, float]:
    """LLM-based salience computation (slower, more accurate)."""
    formatted = _SALIENCE_PROMPT.format(content=content)

    try:
        response = llm.generate(formatted)
        text = response if isinstance(response, str) else str(response)
        start = text.find("{")
        if start >= 0:
            parsed, _ = json.JSONDecoder().raw_decode(text, start)
            valence = max(-1.0, min(1.0, float(parsed.get("valence", 0.0))))
            arousal = max(0.0, min(1.0, float(parsed.get("arousal", 0.0))))
            salience_score = min(1.0, (abs(valence) + arousal) / 2)
            return {
                "sal_valence": round(valence, 3),
                "sal_arousal": round(arousal, 3),
                "sal_salience_score": round(salience_score, 3),
            }
    except Exception as e:
        logger.warning("LLM salience computation failed: %s", e)

    return compute_salience_heuristic(content)


def compute_salience(
    content: str,
    llm: Optional[Any] = None,
    use_llm: bool = False,
) -> Dict[str, float]:
    """Compute salience for a memory's content.

    Returns dict with sal_valence, sal_arousal, sal_salience_score.
    """
    if use_llm and llm:
        return compute_salience_llm(content, llm)
    return compute_salience_heuristic(content)


def salience_decay_modifier(salience_score: float) -> float:
    """Compute decay rate modifier based on salience.

    High-salience memories decay slower.
    Returns a multiplier for the decay lambda (< 1.0 means slower decay).
    """
    return 1.0 - (salience_score * 0.5)
