from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class ExplicitIntent:
    action: Optional[str]
    content: str


_REMEMBER_PATTERNS = [
    r"^\s*(?:please\s+)?remember\b(?: that)?\s*[:,-]?\s*(.+)$",
    r"\b(?:don't|do not)\s+forget\b(?: to)?\s*[:,-]?\s*(.+)$",
    r"\bmake sure to remember\b(?: that)?\s*[:,-]?\s*(.+)$",
]

_FORGET_PATTERNS = [
    r"^\s*(?:forget|delete|remove|erase)\b(?: about| that)?\s*[:,-]?\s*(.+)$",
    r"^\s*(?:don't|do not)\s+remember\b(?: that)?\s*[:,-]?\s*(.+)$",
]


def detect_explicit_intent(text: str) -> ExplicitIntent:
    cleaned = text.strip()
    for pattern in _REMEMBER_PATTERNS:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            content = match.group(1).strip()
            return ExplicitIntent(action="remember", content=content or cleaned)
    for pattern in _FORGET_PATTERNS:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            content = match.group(1).strip()
            return ExplicitIntent(action="forget", content=content or "")
    return ExplicitIntent(action=None, content=cleaned)


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{3}\)?[\s-]?)\d{3}[\s-]?\d{4}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+\w+(?:\s+\w+){0,4}\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct)\b",
    re.IGNORECASE,
)

_NAME_HINT_RE = re.compile(r"\b(?:my name is|call me|i am|i'm)\s+([A-Za-z][A-Za-z'\\-]+(?:\s+[A-Za-z][A-Za-z'\\-]+)?)\b", re.IGNORECASE)

_ID_HINT_RE = re.compile(r"\b(passport|driver'?s license|license number|id number|social security|ssn)\b", re.IGNORECASE)

_HEALTH_HINT_RE = re.compile(
    r"\b(diagnosed|diagnosis|medication|prescription|doctor|clinic|therapy|symptom|allergy|allergic|sick|illness|disease|mental health|depression|anxiety|adhd|diabetes|asthma|blood pressure|migraine)\b",
    re.IGNORECASE,
)

_FINANCE_HINT_RE = re.compile(
    r"\b(bank|account number|routing|iban|swift|credit card|debit card|cvv|salary|income|mortgage|loan|tax|tax id|payment|billing|invoice)\b",
    re.IGNORECASE,
)

_EPHEMERAL_HINT_RE = re.compile(
    r"\b(today|tomorrow|tonight|this morning|this afternoon|this evening|this week|next week|later|in \d+\s*(?:minutes|hours|days)|remind me|schedule|book|call|email|send|buy|pick up|meeting|appointment|todo|to-do|task|for now|currently|at the moment)\b",
    re.IGNORECASE,
)

_PREFERENCE_HINT_RE = re.compile(
    r"\b(prefer|favorite|always|never|like to|love|hate|avoid|must|can't|cannot)\b",
    re.IGNORECASE,
)

_ROUTINE_HINT_RE = re.compile(
    r"\b(every day|every morning|every night|every week|weekly|monthly|on weekends|each week|every weekday)\b",
    re.IGNORECASE,
)

_GOAL_HINT_RE = re.compile(
    r"\b(my goal is|i want to|i plan to|i'm working on|i am working on|long[- ]term)\b",
    re.IGNORECASE,
)


def detect_sensitive_categories(text: str) -> List[str]:
    reasons: List[str] = []
    if _EMAIL_RE.search(text):
        reasons.append("email")
    if _PHONE_RE.search(text):
        reasons.append("phone")
    if _SSN_RE.search(text):
        reasons.append("ssn")
    if _ADDRESS_RE.search(text):
        reasons.append("address")
    if _ID_HINT_RE.search(text):
        reasons.append("id")

    name_match = _NAME_HINT_RE.search(text)
    if name_match:
        candidate = name_match.group(1).strip()
        if candidate and candidate[0].isupper():
            reasons.append("name")

    if _HEALTH_HINT_RE.search(text):
        reasons.append("health")
    if _FINANCE_HINT_RE.search(text):
        reasons.append("finance")

    return sorted(set(reasons))


def is_ephemeral(text: str) -> bool:
    return _EPHEMERAL_HINT_RE.search(text) is not None


def looks_high_confidence(content: str, metadata: Optional[Dict[str, object]] = None) -> bool:
    metadata = metadata or {}
    confidence = _coerce_float(metadata.get("confidence"))
    importance = _coerce_float(metadata.get("importance"))

    if confidence is not None and confidence >= 0.7:
        return True
    if importance is not None and importance >= 0.7:
        return True
    if metadata.get("confirmed") or metadata.get("user_confirmed"):
        return True

    if _PREFERENCE_HINT_RE.search(content):
        return True
    if _ROUTINE_HINT_RE.search(content):
        return True
    if _GOAL_HINT_RE.search(content):
        return True
    return False


def _coerce_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
