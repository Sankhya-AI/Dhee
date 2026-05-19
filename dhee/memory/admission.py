"""Memory admission policy for passive agent observations.

Dhee is the memory layer, so agents should be able to submit rich candidates
without each agent re-implementing quality, retention, and forgetting rules.
This module keeps the hot path deterministic and local: no LLM calls, no network,
and no screenshots/raw media stored here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional


PASSIVE_SOURCES = {
    "chotu_screen_memory",
    "screen_memory",
    "screen_activity",
    "screen_observation",
    "passive_observation",
    "agent_observation",
    "macos_active_window",
    "desktop_observer",
}

PASSIVE_TYPES = {
    "screen_activity",
    "screen_observation",
    "interest_signal",
    "passive_observation",
    "observation",
}

IGNORED_APPS = {
    "Control Center",
    "Dock",
    "Notification Center",
    "SystemUIServer",
    "UserNotificationCenter",
    "Window Server",
    "loginwindow",
}

IGNORED_BUNDLES = {
    "com.apple.controlcenter",
    "com.apple.dock",
    "com.apple.UserNotificationCenter",
    "com.apple.notificationcenterui",
    "com.apple.loginwindow",
    "com.apple.systemuiserver",
}

GENERIC_TITLES = {
    "arc",
    "chrome",
    "codex",
    "google chrome",
    "new tab",
    "safari",
    "youtube",
}

HIGH_CHURN_MARKERS = {
    "claude",
    "codex",
    "com.microsoft.vscode",
    "cursor",
    "visual studio code",
    "windsurf",
}

INTEREST_MARKERS = {
    "chatgpt",
    "claude",
    "codex",
    "course",
    "github",
    "tutorial",
    "video",
    "youtu.be",
    "youtube",
}

COMMON_WORDS = {
    "about",
    "after",
    "again",
    "agent",
    "also",
    "answer",
    "are",
    "because",
    "building",
    "can",
    "chat",
    "check",
    "code",
    "context",
    "currently",
    "data",
    "dhee",
    "doing",
    "file",
    "for",
    "from",
    "github",
    "have",
    "how",
    "memory",
    "model",
    "new",
    "not",
    "now",
    "open",
    "page",
    "repo",
    "screen",
    "search",
    "should",
    "that",
    "the",
    "this",
    "use",
    "user",
    "what",
    "when",
    "with",
    "working",
    "youtube",
}


@dataclass(frozen=True)
class MemoryAdmissionDecision:
    applies: bool
    should_store: bool
    retention_policy: str
    confidence: float
    score: float
    ocr_quality: float
    reasons: List[str]
    promotion_reason: str
    skip_reason: Optional[str] = None
    include_ocr_excerpt: bool = True

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "applies": self.applies,
            "should_store": self.should_store,
            "retention_policy": self.retention_policy,
            "confidence": self.confidence,
            "score": self.score,
            "ocr_quality": self.ocr_quality,
            "reasons": list(self.reasons),
            "promotion_reason": self.promotion_reason,
            "skip_reason": self.skip_reason,
            "include_ocr_excerpt": self.include_ocr_excerpt,
        }


def evaluate_memory_candidate(
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    explicit_remember: bool = False,
) -> MemoryAdmissionDecision:
    """Decide whether a memory candidate should enter Dhee.

    Explicit user memories pass through. Passive observations from screen,
    browser, or wearable agents get admission-scored so Dhee stores useful
    semantic context and rejects transient UI/OCR noise.
    """

    metadata = metadata or {}
    if explicit_remember or not _should_apply_admission(content, metadata):
        return _decision(
            applies=False,
            should_store=True,
            retention_policy=str(metadata.get("retention_policy") or "durable"),
            confidence=_coerce_float(metadata.get("confidence"), 1.0),
            score=1.0,
            ocr_quality=1.0,
            promotion_reason="explicit_or_non_passive",
            reasons=["bypass"],
        )

    evidence = _evidence(metadata)
    app = _first_str(evidence.get("app"), metadata.get("app"), metadata.get("source_app"))
    bundle = _first_str(evidence.get("bundle_id"), metadata.get("bundle_id"))
    title = _first_str(evidence.get("title"), metadata.get("title"))
    dwell_seconds = _coerce_int(evidence.get("dwell_seconds"), metadata.get("dwell_seconds"), 0)
    ocr_text = _first_str(
        metadata.get("ocr_text"),
        evidence.get("ocr_text"),
        _visible_text_from_content(content),
    )
    has_vision = bool(
        metadata.get("vision_summary")
        or evidence.get("vision_summary_sha256")
        or evidence.get("screen_image_available")
    )

    if app in IGNORED_APPS or bundle in IGNORED_BUNDLES:
        return _decision(
            applies=True,
            should_store=False,
            retention_policy="ephemeral",
            confidence=0.0,
            score=0.0,
            ocr_quality=0.0,
            promotion_reason="ignored_system_surface",
            skip_reason="ignored_system_surface",
            reasons=["ignored_app"],
            include_ocr_excerpt=False,
        )

    ocr_quality = _ocr_quality_score(ocr_text)
    common_hits = _ocr_common_hits(ocr_text)
    title_quality = _title_quality(title, app)
    interest = _looks_like_interest(app, title, ocr_text, metadata)
    high_churn = _is_high_churn(app, bundle, title)
    has_specific_context = title_quality >= 0.55 or ocr_quality >= 0.55 or has_vision

    score = 0.18
    score += min(dwell_seconds / 180, 1.0) * 0.22
    score += title_quality * 0.24
    score += ocr_quality * 0.30
    if has_vision:
        score += 0.22
    if interest:
        score += 0.08
    if high_churn and title_quality < 0.45 and not has_vision:
        score -= 0.14
    score = round(max(0.0, min(score, 1.0)), 3)

    reasons: List[str] = []
    if title_quality >= 0.55:
        reasons.append("specific_title")
    if ocr_quality >= 0.55:
        reasons.append("readable_ocr")
    if has_vision:
        reasons.append("vision_summary")
    if dwell_seconds >= 120:
        reasons.append("long_dwell")
    elif dwell_seconds >= 30:
        reasons.append("meaningful_dwell")
    if interest:
        reasons.append("interest_signal")

    if high_churn and title_quality < 0.45 and not has_vision and dwell_seconds < 60 and common_hits < 2:
        return _decision(
            applies=True,
            should_store=False,
            retention_policy="ephemeral",
            confidence=0.0,
            score=score,
            ocr_quality=ocr_quality,
            promotion_reason="high_churn_ocr_noise",
            skip_reason="low_quality_signal",
            reasons=reasons,
            include_ocr_excerpt=False,
        )
    if not has_specific_context and dwell_seconds < 30:
        return _decision(
            applies=True,
            should_store=False,
            retention_policy="ephemeral",
            confidence=0.0,
            score=score,
            ocr_quality=ocr_quality,
            promotion_reason="no_specific_context",
            skip_reason="low_quality_signal",
            reasons=reasons,
            include_ocr_excerpt=False,
        )
    if ocr_text and ocr_quality < 0.22 and title_quality < 0.45 and not has_vision:
        return _decision(
            applies=True,
            should_store=False,
            retention_policy="ephemeral",
            confidence=0.0,
            score=score,
            ocr_quality=ocr_quality,
            promotion_reason="ocr_noise",
            skip_reason="low_ocr_quality",
            reasons=reasons,
            include_ocr_excerpt=False,
        )

    durable = (
        (has_vision and dwell_seconds >= 30)
        or (dwell_seconds >= 180 and score >= 0.52)
        or (interest and dwell_seconds >= 60 and score >= 0.58)
        or (ocr_quality >= 0.74 and dwell_seconds >= 90)
    )
    retention_policy = "durable" if durable else "session"
    confidence = round(max(0.45, min(0.94, 0.42 + score * 0.48)), 3)
    return _decision(
        applies=True,
        should_store=True,
        retention_policy=retention_policy,
        confidence=confidence,
        score=score,
        ocr_quality=ocr_quality,
        promotion_reason="durable_quality_gate" if durable else "session_until_promoted",
        reasons=reasons,
        include_ocr_excerpt=ocr_quality >= 0.38 and not has_vision,
    )


def admission_expiration_date(retention_policy: str) -> Optional[str]:
    """Return a coarse ISO date for non-durable admission retention."""

    policy = (retention_policy or "").lower()
    if policy == "ephemeral":
        return (date.today() + timedelta(days=1)).isoformat()
    if policy == "session":
        return (date.today() + timedelta(days=7)).isoformat()
    if policy == "short":
        return (date.today() + timedelta(days=30)).isoformat()
    return None


def forget_reason_for_memory(memory: Dict[str, Any]) -> Optional[str]:
    """Return a reason when an existing memory should be forgotten."""

    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
    if not _should_apply_admission(str(memory.get("memory") or ""), metadata):
        return None
    admission = metadata.get("dhee_admission") if isinstance(metadata, dict) else None
    if isinstance(admission, dict):
        try:
            score = float(admission.get("score"))
        except (TypeError, ValueError):
            score = 1.0
        if admission.get("should_store") is False:
            return f"admission:{admission.get('skip_reason') or 'rejected'}"
        if score < 0.25:
            return "admission:low_quality"
        return None

    decision = evaluate_memory_candidate(
        str(memory.get("memory") or ""),
        metadata,
        explicit_remember=False,
    )
    if decision.applies and not decision.should_store:
        return f"admission:{decision.skip_reason or 'rejected'}"
    return None


def sanitize_admitted_content(content: str, decision: MemoryAdmissionDecision) -> str:
    """Trim noisy raw OCR from admitted passive memories when Dhee does not need it."""

    if not decision.applies or decision.include_ocr_excerpt:
        return content
    return _strip_visible_text(content)


def _should_apply_admission(content: str, metadata: Dict[str, Any]) -> bool:
    if metadata.get("admission") is False or metadata.get("dhee_admission_bypass"):
        return False
    evidence = _evidence(metadata)
    source = str(metadata.get("source") or metadata.get("source_app") or "").strip().lower()
    mem_type = str(metadata.get("type") or metadata.get("memory_type") or "").strip().lower()
    kind = str(evidence.get("kind") or "").strip().lower()
    if source in PASSIVE_SOURCES or mem_type in PASSIVE_TYPES or kind in PASSIVE_SOURCES:
        return True
    if any(key in evidence for key in ("app", "bundle_id", "title", "dwell_seconds", "ocr_text_sha256")):
        return True
    lowered = content.lower()
    return "visible screen activity" in lowered or "active screen" in lowered


def _decision(**kwargs: Any) -> MemoryAdmissionDecision:
    return MemoryAdmissionDecision(**kwargs)


def _evidence(metadata: Dict[str, Any]) -> Dict[str, Any]:
    evidence = metadata.get("evidence")
    return evidence if isinstance(evidence, dict) else {}


def _first_str(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(*values: Any) -> int:
    default = int(values[-1]) if values else 0
    for value in values[:-1]:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _visible_text_from_content(content: str) -> str:
    for marker in ("Visible text excerpt:", "Visible text:", "Selected text:"):
        if marker in content:
            return content.split(marker, 1)[1].split("Visual summary:", 1)[0].strip()
    return ""


def _strip_visible_text(content: str) -> str:
    for marker in ("Visible text excerpt:", "Visible text:"):
        if marker not in content:
            continue
        before, after = content.split(marker, 1)
        if "Visual summary:" in after:
            _, rest = after.split("Visual summary:", 1)
            return (before.rstrip() + "\nVisual summary:\n" + rest.strip()).strip()
        return before.rstrip()
    return content


def _ocr_quality_score(text: str) -> float:
    compact = " ".join(str(text or "").split())
    if len(compact) < 24:
        return 0.0
    chars = len(compact)
    alnum_ratio = sum(ch.isalnum() for ch in compact) / chars
    allowed = ".,:;!?()[]{}'\"/-_@#%+&"
    weird_ratio = sum(not (ch.isalnum() or ch.isspace() or ch in allowed) for ch in compact) / chars
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'_-]{2,}", compact)
    if not tokens:
        return 0.0
    good_tokens = [token for token in tokens if _looks_like_word(token)]
    token_quality = len(good_tokens) / len(tokens)
    unique_ratio = len({token.lower() for token in tokens}) / len(tokens)
    common_ratio = min(_ocr_common_hits(compact) / 8, 1.0)
    length_score = min(chars / 500, 1.0)
    score = (
        0.16
        + alnum_ratio * 0.18
        + token_quality * 0.28
        + unique_ratio * 0.14
        + common_ratio * 0.12
        + length_score * 0.12
        - weird_ratio * 0.35
    )
    if len(tokens) < 6:
        score -= 0.12
    return round(max(0.0, min(score, 1.0)), 3)


def _ocr_common_hits(text: str) -> int:
    return sum(
        1
        for token in re.findall(r"[A-Za-z][A-Za-z0-9'_-]{2,}", str(text or ""))
        if token.lower() in COMMON_WORDS
    )


def _title_quality(title: str, app: str) -> float:
    normalized = str(title or "").strip()
    if _is_generic_title(normalized, app):
        return 0.0
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]{1,}", normalized)
    if not words:
        return 0.0
    score = min(len(normalized) / 80, 0.45) + min(len(words) / 8, 0.35) + 0.2
    if any(marker in normalized.lower() for marker in ("chatgpt", "github", "google", "youtube")):
        score += 0.08
    return round(max(0.0, min(score, 1.0)), 3)


def _is_generic_title(title: str, app: str) -> bool:
    normalized_title = str(title or "").strip().lower()
    normalized_app = str(app or "").strip().lower()
    if not normalized_title:
        return True
    return normalized_title == normalized_app or normalized_title in GENERIC_TITLES


def _looks_like_word(token: str) -> bool:
    letters = re.sub(r"[^a-z]", "", token.lower())
    if len(letters) < 3:
        return True
    if not re.search(r"[aeiou]", letters):
        return False
    if re.search(r"[^aeiou]{6,}", letters):
        return False
    if len(set(letters)) <= 2 and len(letters) >= 6:
        return False
    return True


def _is_high_churn(app: str, bundle: str, title: str) -> bool:
    haystack = " ".join((app or "", bundle or "", title or "")).lower()
    return any(marker in haystack for marker in HIGH_CHURN_MARKERS)


def _looks_like_interest(app: str, title: str, ocr_text: str, metadata: Dict[str, Any]) -> bool:
    haystack = " ".join(
        (
            app or "",
            title or "",
            ocr_text or "",
            str(metadata.get("source") or ""),
            str(metadata.get("type") or ""),
        )
    ).lower()
    return any(marker in haystack for marker in INTEREST_MARKERS)
