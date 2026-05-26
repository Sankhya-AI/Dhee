"""Admission and redaction policy for voice-agent memory events."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"\b\d{6}\b"),
    re.compile(r"(?i)\b(password|passcode|api key|secret|token)\b[:\s]+[^\s,.;]+"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
]


@dataclass
class Admission:
    should_store: bool
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def contains_voice_secret(content: str) -> bool:
    """Return True when text contains data Dhee should not retain."""

    text = content or ""
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def redact_voice_content(content: str) -> str:
    """Redact high-risk voice content before it can enter memory."""

    text = content or ""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def admit_voice_event(event: dict[str, Any]) -> Admission:
    """Decide whether a noisy voice event is worth storing."""

    event_type = str(event.get("type") or "")
    content = str(event.get("content") or "").strip()
    lowered = content.lower()

    if not content:
        return Admission(False, reason="empty")

    if contains_voice_secret(content):
        return Admission(False, reason="sensitive_content")

    durable_markers = [
        "remember",
        "i prefer",
        "my preference",
        "next time",
        "follow up",
        "call me",
        "message me",
        "whatsapp",
        "email me",
        "i decided",
        "we decided",
        "correct that",
        "actually",
        "my name is",
        "don't ask me",
        "do not ask me",
    ]

    if any(marker in lowered for marker in durable_markers):
        return Admission(
            True,
            content=redact_voice_content(content),
            metadata={"retention_policy": "durable", "channel": "voice"},
            reason="durable_voice_marker",
        )

    if event_type in {"voice.call_summary", "voice.followup", "voice.correction"}:
        return Admission(
            True,
            content=redact_voice_content(content),
            metadata={"retention_policy": "durable", "channel": "voice"},
            reason=event_type,
        )

    return Admission(False, reason="voice_noise")
