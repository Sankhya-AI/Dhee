"""Deterministic memory-quality contract for Dhee writes and recall.

This module keeps product/user truths from being treated like ordinary
episodic observations. It is intentionally rule-based and cheap: the write
pipeline can apply it before LLM enrichment, and the search pipeline can use
the same labels for ranking and explanation.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


CANONICAL_NAMESPACE = "canonical_personal"
PASSIVE_NAMESPACE = "passive_screen"
TEST_NAMESPACE = "test"
EVIDENCE_NAMESPACE = "evidence"
PROJECT_NAMESPACE = "project_context"
OPERATIONAL_NAMESPACE = "operational"

CANONICAL_MEMORY_TYPES = {
    "profile",
    "goal",
    "constraint",
    "preference",
    "decision",
    "project",
    "style",
    "product",
    "product_philosophy",
    "canonical_personal",
}

STRUCTURED_INTERNAL_TYPES = {
    "project",
    "project_status",
    "project_tag",
    "warroom",
    "warroom_message",
    "task",
    "note",
    "procedural",
}

PASSIVE_SOURCES = {
    "chotu_screen_memory",
    "screen_memory",
    "screen_activity",
    "screen_capture",
    "passive_screen",
    "passive_observation",
}

PASSIVE_TYPES = {
    "screen_activity",
    "screen_observation",
    "interest_signal",
    "passive_observation",
    "observation",
}

TEST_SOURCE_HINTS = {
    "pytest",
    "test",
    "unit_test",
    "integration_test",
    "fixture",
    "memory_test",
}

EVIDENCE_TYPES = {
    "artifact",
    "markdown",
    "readme",
    "screenshot",
    "tweet",
    "video",
    "document",
    "raw_evidence",
    "evidence",
}

_WORD_RE = re.compile(r"\b[a-z0-9][a-z0-9_-]{2,}\b", re.IGNORECASE)
_TEST_FIXTURE_RE = re.compile(
    r"^\s*(?:"
    r"memory\s+(?:\d{1,4}|one|two|three|four|five|item\s+\d{1,4})|"
    r"(?:default\s+user|persistent)\s+memory|"
    r"user\s+[a-z]\s+memory|"
    r"(?:exact|norm|boost|first|second|shared|preserve|dedup)_[a-f0-9]{6,}|"
    r"history\s+test\s+[a-f0-9]{6,}|"
    r"(?:boost|cache)\s+test|"
    r"unique\s+content\s+[a-z0-9_-]{3,}|"
    r"(?:some\s+data\s+to\s+search|data\s+for\s+eviction\s+test)|"
    r"(?:caching\s+is\s+good|hello\s+world|to\s+be\s+deleted|original\s+content|updated\s+content)|"
    r"content(?:\s+[a-z0-9_-]{3,})?|"
    r"i\s+like\s+python(?:\s+[a-f0-9]{6,})?|"
    r"test\s+content\s+[a-z0-9_-]{3,}|"
    r"test\s+memory(?:\s+about\b.*)?|"
    r"agent\s+memory|"
    r"important\s+fact"
    r")\s*$",
    re.IGNORECASE,
)
_OPERATIONAL_SUCCESS_RE = re.compile(
    r"^\s*(?:edited|wrote|created|updated|modified|touched)\s+(.{1,240})\s*$",
    re.IGNORECASE,
)
_OPERATIONAL_TRANSPORT_RE = re.compile(
    r"^\s*(?:ran|read|grep|glob|searched|opened|listed|codex running|claude running)\b",
    re.IGNORECASE,
)
_GOAL_RE = re.compile(
    r"\b(my goal is|our goal is|goal is|i want to|we want to|i plan to|we plan to|"
    r"working on|personal assistant goal|product goal|north star|objective)\b",
    re.IGNORECASE,
)
_PREFERENCE_RE = re.compile(
    r"\b(prefer|preference|favorite|always|never|like to|love|hate|avoid|must|"
    r"cannot|can't|style preference|tone preference)\b",
    re.IGNORECASE,
)
_STYLE_RE = re.compile(
    r"\b(writing style|communication style|tone|voice|style profile|write like|"
    r"speaks?|responds?)\b",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"\b(decided|decision|we will|i will|canonical decision|policy|principle)\b",
    re.IGNORECASE,
)
_CONSTRAINT_RE = re.compile(
    r"\b(constraint|do not|don't|never|must not|required|requirement|guardrail)\b",
    re.IGNORECASE,
)
_PROJECT_RE = re.compile(
    r"\b(project|product|roadmap|architecture|strategy|mission|philosophy|chotu)\b",
    re.IGNORECASE,
)
_PERSONAL_SUBJECT_RE = re.compile(
    r"\b(my|the user|user['’]s|user prefers|technology preference|writing style profile|"
    r"style profile|personal assistant|product goal|chotu goal|chotu preference)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(action|todo|follow up|follow-up|must|should|need to|needs to|required|fix|implement|ship|verify)\b",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:\s+[A-Z][A-Za-z0-9_]+){0,3}\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

_TOPIC_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "being",
    "between",
    "could",
    "from",
    "have",
    "into",
    "just",
    "more",
    "need",
    "only",
    "over",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "through",
    "want",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
    "your",
}


@dataclass(frozen=True)
class MemoryQuality:
    memory_class: str
    canonical_kind: Optional[str]
    namespace: str
    memory_type: str
    layer: str
    retention_policy: str
    strength_floor: float
    strength_cap: Optional[float]
    decay_lambda: Optional[float]
    confidence_floor: float
    importance_floor: float
    suppress_from_default_recall: bool
    search_multiplier: float
    decision_risk: str
    reasons: Tuple[str, ...]

    @property
    def is_canonical(self) -> bool:
        return self.memory_class == "canonical_personal"

    @property
    def is_passive(self) -> bool:
        return self.memory_class == "passive_screen"

    @property
    def is_test(self) -> bool:
        return self.memory_class == "test_fixture"

    @property
    def is_operational(self) -> bool:
        return self.memory_class == "operational_event"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_metadata(metadata: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Flatten legacy JSON-string metadata wrappers without losing overlays."""
    current: Dict[str, Any] = dict(metadata or {})
    for _ in range(4):
        raw = current.get("legacy_metadata_raw")
        if not isinstance(raw, str):
            break
        try:
            parsed = json.loads(raw)
        except Exception:
            break
        if not isinstance(parsed, dict):
            break
        overlay = {
            key: value
            for key, value in current.items()
            if key not in {"legacy_metadata_raw", "legacy_metadata_type"}
        }
        current = {**parsed, **overlay}
    return current


def _lower_values(metadata: Mapping[str, Any], keys: Sequence[str]) -> Set[str]:
    values: Set[str] = set()
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            values.update(str(item).strip().lower() for item in value if str(item).strip())
        else:
            text = str(value).strip().lower()
            if text:
                values.add(text)
    return values


def _metadata_bool(metadata: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, bool):
            if value:
                return True
        elif isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        elif value not in (None, "", 0):
            return True
    return False


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _tokens(text: str) -> Set[str]:
    return {
        match.group(0).lower()
        for match in _WORD_RE.finditer(text or "")
        if len(match.group(0)) > 2
    }


def _is_passive_observation(metadata: Mapping[str, Any]) -> bool:
    source_values = _lower_values(
        metadata,
        ("source", "source_type", "source_app", "type", "memory_type"),
    )
    if source_values & PASSIVE_SOURCES:
        return True
    if source_values & PASSIVE_TYPES:
        return True
    if _metadata_bool(metadata, "dhee_passive_observation", "passive_observation"):
        return True
    evidence = metadata.get("evidence")
    if isinstance(evidence, Mapping):
        evidence_kind = str(evidence.get("kind") or "").strip().lower()
        if evidence_kind in {"screen_context", "screen", "screen_observation"}:
            return True
    return False


def _is_artifact_like_metadata(metadata: Mapping[str, Any]) -> bool:
    kind = str(metadata.get("kind") or metadata.get("type") or "").strip().lower()
    source = str(metadata.get("source") or metadata.get("source_type") or "").strip().lower()
    if kind in {
        "doc_chunk",
        "artifact_chunk",
        "file_touched",
        "tool_event",
        "session_log",
        "codex_event",
        "claude_code_event",
    }:
        return True
    if source in {"claude_code_hook", "codex_hook", "session_log", "artifact", "markdown", "readme"}:
        return True
    return any(metadata.get(key) for key in ("source_path", "artifact_id", "chunk_index"))


def _is_operational_event(content: str, metadata: Mapping[str, Any]) -> bool:
    """True for transport-level agent/tool events that are evidence, not belief."""
    kind = str(metadata.get("kind") or metadata.get("type") or "").strip().lower()
    tool = str(metadata.get("tool") or metadata.get("tool_name") or metadata.get("native_tool") or "").strip().lower()
    source = str(metadata.get("source") or metadata.get("source_type") or metadata.get("source_app") or "").strip().lower()
    success = metadata.get("success")
    text = " ".join(str(content or "").strip().split())

    if kind in {"file_touched", "tool_event", "session_log", "codex_event", "claude_code_event"}:
        return str(success).lower() not in {"false", "0", "no"}
    if source in {"claude_code_hook", "codex_hook", "session_log"} and tool not in {"bash", "bashoutput"}:
        return str(success).lower() not in {"false", "0", "no"}
    success_match = _OPERATIONAL_SUCCESS_RE.match(text)
    if success_match:
        target = success_match.group(1).strip()
        if (
            "/" in target
            or "\\" in target
            or target.startswith("~")
            or re.search(r"\.[A-Za-z0-9]{1,8}(?:$|[\s:])", target)
        ):
            return True
    if _OPERATIONAL_TRANSPORT_RE.match(text):
        return "failed" not in text.lower()
    return False


def evidence_kind_from_metadata(metadata: Mapping[str, Any]) -> str:
    evidence = metadata.get("evidence")
    if isinstance(evidence, Mapping):
        kind = str(evidence.get("kind") or evidence.get("type") or "").strip().lower()
        if kind:
            return kind

    for key in ("evidence_kind", "artifact_kind", "source_type", "source", "type"):
        value = str(metadata.get(key) or "").strip().lower()
        if value in EVIDENCE_TYPES or value in {"screen_context", "screen", "screen_observation"}:
            return value

    pathish = " ".join(
        str(metadata.get(key) or "")
        for key in ("path", "source_path", "filename", "artifact_path", "url")
    ).lower()
    if "readme" in pathish or pathish.endswith(".md") or ".md#" in pathish:
        return "markdown"
    if any(pathish.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
        return "screenshot"
    if any(pathish.endswith(ext) for ext in (".mp4", ".mov", ".webm")):
        return "video"
    if "twitter.com" in pathish or "x.com/" in pathish:
        return "tweet"
    return "raw_evidence"


def _first_matching_sentence(content: str, patterns: Sequence[re.Pattern[str]]) -> str:
    for sentence in _SENTENCE_SPLIT_RE.split((content or "")[:6000]):
        clean = " ".join(sentence.strip().split())
        if not clean:
            continue
        if any(pattern.search(clean) for pattern in patterns):
            return clean[:240]
    return ""


def _extract_topics(content: str, metadata: Mapping[str, Any], *, limit: int = 12) -> List[str]:
    existing = metadata.get("topics")
    if isinstance(existing, (list, tuple, set)):
        values = [str(item).strip().lower() for item in existing if str(item).strip()]
        if values:
            return values[:limit]
    topic_tokens = []
    for raw in _WORD_RE.findall(content or ""):
        token = raw.lower()
        if len(token) > 2 and token not in _TOPIC_STOPWORDS and not token.isdigit():
            topic_tokens.append(token)
    counts = Counter(topic_tokens)
    return [token for token, _count in counts.most_common(limit)]


def _extract_entities(content: str, metadata: Mapping[str, Any], *, limit: int = 12) -> List[str]:
    entities: List[str] = []
    existing = metadata.get("entities")
    if isinstance(existing, (list, tuple, set)):
        entities.extend(str(item).strip() for item in existing if str(item).strip())
    for match in _ENTITY_RE.finditer((content or "")[:8000]):
        entity = " ".join(match.group(0).split())
        if entity.lower() in {"i", "we", "my", "our", "the", "this", "that"}:
            continue
        if len(entity) > 80:
            continue
        if entity not in entities:
            entities.append(entity)
        if len(entities) >= limit:
            break
    return entities[:limit]


def _source_quality(metadata: Mapping[str, Any], confidence: float) -> str:
    evidence = metadata.get("evidence") if isinstance(metadata.get("evidence"), Mapping) else {}
    has_hash = any(
        metadata.get(key) or (isinstance(evidence, Mapping) and evidence.get(key))
        for key in ("hash", "source_hash", "content_hash", "artifact_hash", "sha256")
    )
    has_origin = any(
        metadata.get(key) or (isinstance(evidence, Mapping) and evidence.get(key))
        for key in ("artifact_id", "source_path", "path", "url", "source_event_id", "kind")
    )
    if has_hash and has_origin:
        return "high"
    if has_hash or has_origin:
        return "medium"
    return "low"


def distill_evidence_metadata(
    content: str,
    metadata: Mapping[str, Any],
    quality: MemoryQuality,
) -> Dict[str, Any]:
    """Build bounded structured evidence metadata without inventing beliefs."""
    existing_raw = metadata.get("evidence_distillation")
    existing = dict(existing_raw) if isinstance(existing_raw, Mapping) else {}
    confidence = _coerce_float(metadata.get("confidence"))
    if confidence is None:
        confidence = quality.confidence_floor

    why_user_cares = (
        str(existing.get("why_user_cares") or metadata.get("why_user_cares") or "").strip()
        or _first_matching_sentence(content, (_GOAL_RE, _PREFERENCE_RE, _STYLE_RE, _DECISION_RE, _CONSTRAINT_RE))
    )

    decision_relevance = str(existing.get("decision_relevance") or "").strip().lower()
    if not decision_relevance:
        if any(pattern.search(content or "") for pattern in (_GOAL_RE, _DECISION_RE, _CONSTRAINT_RE, _STYLE_RE)):
            decision_relevance = "high"
        elif _PROJECT_RE.search(content or ""):
            decision_relevance = "medium"
        else:
            decision_relevance = "low"

    actionability = str(existing.get("actionability") or "").strip().lower()
    if not actionability:
        if _ACTION_RE.search(content or ""):
            actionability = "high"
        elif decision_relevance in {"high", "medium"}:
            actionability = "medium"
        else:
            actionability = "low"

    contradictions = existing.get("contradictions", metadata.get("contradictions", []))
    if isinstance(contradictions, str):
        contradictions = [contradictions] if contradictions.strip() else []
    elif not isinstance(contradictions, list):
        contradictions = []

    return {
        "why_user_cares": why_user_cares[:240],
        "entities": _extract_entities(content, metadata),
        "topics": _extract_topics(content, metadata),
        "decision_relevance": decision_relevance,
        "actionability": actionability,
        "source_quality": str(existing.get("source_quality") or _source_quality(metadata, float(confidence))),
        "confidence": round(float(confidence), 3),
        "retention": quality.retention_policy,
        "contradictions": contradictions[:12],
    }


def _is_test_fixture(
    content: str,
    metadata: Mapping[str, Any],
    *,
    explicit_remember: bool = False,
) -> bool:
    source_values = _lower_values(
        metadata,
        ("source", "source_type", "source_app", "namespace", "type", "memory_type"),
    )
    if source_values & TEST_SOURCE_HINTS:
        return True
    if _metadata_bool(metadata, "test_fixture", "fixture", "is_test", "dhee_test_memory"):
        return True
    if explicit_remember:
        return False
    compact = content.strip()
    return len(compact) <= 96 and _TEST_FIXTURE_RE.match(compact) is not None


def infer_canonical_kind(
    content: str,
    metadata: Mapping[str, Any],
    categories: Optional[Iterable[str]] = None,
    *,
    explicit_remember: bool = False,
) -> Optional[str]:
    explicit_kind = str(metadata.get("canonical_kind") or metadata.get("memory_kind") or metadata.get("kind") or "").strip().lower()
    if explicit_kind in CANONICAL_MEMORY_TYPES:
        return "profile" if explicit_kind == "canonical_personal" else explicit_kind

    explicit_type = str(metadata.get("memory_type") or "").strip().lower()
    if explicit_type in CANONICAL_MEMORY_TYPES:
        return "profile" if explicit_type == "canonical_personal" else explicit_type

    category_values = {str(c).strip().lower() for c in (categories or []) if str(c).strip()}
    for value in category_values:
        if value in CANONICAL_MEMORY_TYPES:
            return "profile" if value == "canonical_personal" else value

    text = content or ""
    if _GOAL_RE.search(text):
        return "goal"
    if _DECISION_RE.search(text):
        return "decision"
    if _CONSTRAINT_RE.search(text):
        return "constraint"
    if _STYLE_RE.search(text):
        return "style"
    if _PREFERENCE_RE.search(text):
        return "preference"
    if explicit_remember and _PROJECT_RE.search(text):
        return "project"

    if _metadata_bool(metadata, "canonical", "canonical_personal", "explicit_memory"):
        if _PROJECT_RE.search(text):
            return "project"
        return "profile"

    source_type = str(metadata.get("source_type") or metadata.get("source") or "").strip().lower()
    if source_type in {"product_philosophy", "canonical_doc", "profile_doc", "style_doc"}:
        if "style" in source_type:
            return "style"
        if "product" in source_type:
            return "product_philosophy"
        return "profile"

    return None


def classify_memory_quality(
    content: str,
    metadata: Optional[Mapping[str, Any]] = None,
    categories: Optional[Iterable[str]] = None,
    *,
    explicit_remember: bool = False,
) -> MemoryQuality:
    metadata = _normalized_metadata(metadata)
    content = content or ""
    reasons: List[str] = []

    if _is_test_fixture(content, metadata, explicit_remember=explicit_remember):
        return MemoryQuality(
            memory_class="test_fixture",
            canonical_kind=None,
            namespace=TEST_NAMESPACE,
            memory_type="test_fixture",
            layer="sml",
            retention_policy="ephemeral",
            strength_floor=0.01,
            strength_cap=0.05,
            decay_lambda=1.0,
            confidence_floor=0.1,
            importance_floor=0.0,
            suppress_from_default_recall=True,
            search_multiplier=0.02,
            decision_risk="high",
            reasons=("test_fixture_signature",),
        )

    if _is_operational_event(content, metadata):
        return MemoryQuality(
            memory_class="operational_event",
            canonical_kind=None,
            namespace=OPERATIONAL_NAMESPACE,
            memory_type="operational_event",
            layer="sml",
            retention_policy=str(metadata.get("retention_policy") or "session"),
            strength_floor=0.0,
            strength_cap=0.05,
            decay_lambda=0.5,
            confidence_floor=0.2,
            importance_floor=0.0,
            suppress_from_default_recall=True,
            search_multiplier=0.05,
            decision_risk="medium",
            reasons=("tool_transport_event",),
        )

    explicit_type = str(metadata.get("memory_type") or "").strip().lower()
    if explicit_type in STRUCTURED_INTERNAL_TYPES or any(
        key in metadata
        for key in (
            "project_name",
            "status_project_id",
            "tag_project_id",
            "warroom_id",
            "task_id",
        )
    ):
        return MemoryQuality(
            memory_class="project_context",
            canonical_kind="project" if explicit_type.startswith("project") else explicit_type or None,
            namespace=str(metadata.get("namespace") or PROJECT_NAMESPACE),
            memory_type=explicit_type or str(metadata.get("memory_type") or "semantic"),
            layer="lml" if explicit_type.startswith("project") else "sml",
            retention_policy=str(metadata.get("retention_policy") or "durable"),
            strength_floor=0.7 if explicit_type.startswith("project") else 0.4,
            strength_cap=None,
            decay_lambda=0.04 if explicit_type.startswith("project") else None,
            confidence_floor=0.7,
            importance_floor=0.65,
            suppress_from_default_recall=False,
            search_multiplier=1.15,
            decision_risk="medium",
            reasons=("structured_internal_memory",),
        )

    passive = _is_passive_observation(metadata)
    source_values = _lower_values(metadata, ("source", "source_type", "type"))
    canonical_kind = infer_canonical_kind(
        content,
        metadata,
        categories,
        explicit_remember=explicit_remember,
    )

    source_type = str(metadata.get("source_type") or metadata.get("source") or "").strip().lower()
    strong_explicit_personal = (
        explicit_remember
        or _metadata_bool(metadata, "policy_explicit", "explicit_remember")
        or _metadata_bool(metadata, "canonical", "canonical_personal", "explicit_memory")
        or source_type in {"product_philosophy", "canonical_doc", "profile_doc", "style_doc"}
    )
    artifact_like = _is_artifact_like_metadata(metadata)
    explicit_personal = strong_explicit_personal or (
        not artifact_like
        and (
            _metadata_bool(metadata, "user_provided")
            or _PERSONAL_SUBJECT_RE.search(content or "") is not None
        )
    )

    if source_values & EVIDENCE_TYPES and not strong_explicit_personal:
        return MemoryQuality(
            memory_class="evidence_artifact",
            canonical_kind=None,
            namespace=EVIDENCE_NAMESPACE,
            memory_type="episodic",
            layer="sml",
            retention_policy=str(metadata.get("retention_policy") or "durable"),
            strength_floor=0.35,
            strength_cap=0.8,
            decay_lambda=0.12,
            confidence_floor=0.5,
            importance_floor=0.35,
            suppress_from_default_recall=False,
            search_multiplier=0.8,
            decision_risk="medium",
            reasons=("raw_evidence_artifact",),
        )

    if canonical_kind and explicit_personal and not passive:
        reasons.append(f"canonical_{canonical_kind}")
        if explicit_personal:
            reasons.append("explicit_personal_signal")
        return MemoryQuality(
            memory_class="canonical_personal",
            canonical_kind=canonical_kind,
            namespace=CANONICAL_NAMESPACE,
            memory_type="semantic",
            layer="lml",
            retention_policy="durable",
            strength_floor=0.92,
            strength_cap=None,
            decay_lambda=0.0,
            confidence_floor=0.9,
            importance_floor=0.9,
            suppress_from_default_recall=False,
            search_multiplier=1.75,
            decision_risk="low",
            reasons=tuple(reasons),
        )

    if passive:
        return MemoryQuality(
            memory_class="passive_screen",
            canonical_kind=None,
            namespace=PASSIVE_NAMESPACE,
            memory_type="episodic",
            layer="sml",
            retention_policy=str(metadata.get("retention_policy") or "session"),
            strength_floor=0.0,
            strength_cap=0.55,
            decay_lambda=0.18,
            confidence_floor=0.3,
            importance_floor=0.2,
            suppress_from_default_recall=True,
            search_multiplier=0.35,
            decision_risk="medium",
            reasons=("passive_observation_source",),
        )

    if source_values & EVIDENCE_TYPES:
        return MemoryQuality(
            memory_class="evidence_artifact",
            canonical_kind=None,
            namespace=EVIDENCE_NAMESPACE,
            memory_type="episodic",
            layer="sml",
            retention_policy=str(metadata.get("retention_policy") or "durable"),
            strength_floor=0.35,
            strength_cap=0.8,
            decay_lambda=0.12,
            confidence_floor=0.5,
            importance_floor=0.35,
            suppress_from_default_recall=False,
            search_multiplier=0.8,
            decision_risk="medium",
            reasons=("raw_evidence_artifact",),
        )

    if canonical_kind:
        # Non-explicit but assertive project/user facts still deserve the
        # project lane instead of being lost in passive/episodic noise.
        return MemoryQuality(
            memory_class="project_context",
            canonical_kind=canonical_kind,
            namespace=PROJECT_NAMESPACE,
            memory_type="semantic",
            layer="lml",
            retention_policy="durable",
            strength_floor=0.7,
            strength_cap=None,
            decay_lambda=0.04,
            confidence_floor=0.7,
            importance_floor=0.65,
            suppress_from_default_recall=False,
            search_multiplier=1.25,
            decision_risk="medium",
            reasons=(f"project_{canonical_kind}",),
        )

    return MemoryQuality(
        memory_class="ordinary",
        canonical_kind=None,
        namespace=str(metadata.get("namespace") or "default"),
        memory_type=str(metadata.get("memory_type") or "semantic"),
        layer="sml",
        retention_policy=str(metadata.get("retention_policy") or "normal"),
        strength_floor=0.0,
        strength_cap=None,
        decay_lambda=None,
        confidence_floor=0.0,
        importance_floor=0.0,
        suppress_from_default_recall=False,
        search_multiplier=1.0,
        decision_risk="medium",
        reasons=("ordinary_memory",),
    )


def apply_memory_quality_contract(
    content: str,
    metadata: Optional[Mapping[str, Any]],
    categories: Optional[Iterable[str]] = None,
    *,
    explicit_remember: bool = False,
) -> Tuple[Dict[str, Any], MemoryQuality]:
    """Return metadata updated with Dhee's memory-quality contract."""
    updated = _normalized_metadata(metadata)
    quality = classify_memory_quality(
        content,
        updated,
        categories,
        explicit_remember=explicit_remember,
    )

    updated["dhee_memory_class"] = quality.memory_class
    updated["dhee_quality_reasons"] = list(quality.reasons)
    updated["dhee_decision_risk"] = quality.decision_risk
    updated["dhee_search_multiplier"] = quality.search_multiplier
    updated["retention_policy"] = quality.retention_policy
    updated["namespace"] = quality.namespace
    updated["memory_type"] = quality.memory_type
    if quality.canonical_kind:
        updated["canonical_kind"] = quality.canonical_kind
    if quality.is_canonical:
        updated["canonical_personal"] = True
        updated["policy_explicit"] = True
        updated["decay_class"] = "stable"
    if quality.is_passive:
        updated["dhee_passive_observation"] = True
    if quality.is_test:
        updated["dhee_test_memory"] = True
        updated["suppress_from_default_recall"] = True
    if quality.is_operational:
        updated["dhee_operational_event"] = True
        updated["suppress_from_default_recall"] = True
        updated["raw_evidence"] = True
        updated["evidence_kind"] = "operational_event"
    if quality.memory_class in {"evidence_artifact", "passive_screen"}:
        updated["raw_evidence"] = True
        updated["evidence_kind"] = evidence_kind_from_metadata(updated)

    confidence = _coerce_float(updated.get("confidence"))
    if confidence is None or confidence < quality.confidence_floor:
        updated["confidence"] = quality.confidence_floor
    importance = _coerce_float(updated.get("importance"))
    if importance is None or importance < quality.importance_floor:
        updated["importance"] = quality.importance_floor
    if quality.decay_lambda is not None:
        updated["decay_lambda"] = quality.decay_lambda
    if quality.memory_class in {"evidence_artifact", "passive_screen"}:
        updated["evidence_distillation"] = distill_evidence_metadata(content, updated, quality)
    return updated, quality


def enforce_quality_strength(strength: float, quality: MemoryQuality) -> float:
    value = float(strength)
    if quality.strength_floor:
        value = max(value, quality.strength_floor)
    if quality.strength_cap is not None:
        value = min(value, quality.strength_cap)
    return value


def enforce_quality_layer(layer: str, quality: MemoryQuality) -> str:
    if quality.layer in {"sml", "lml"}:
        return quality.layer
    return layer if layer in {"sml", "lml"} else "sml"


def is_protected_canonical_memory(memory: Mapping[str, Any]) -> bool:
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), Mapping) else {}
    namespace = str(memory.get("namespace") or metadata.get("namespace") or "").strip()
    memory_type = str(memory.get("memory_type") or metadata.get("memory_type") or "").strip().lower()
    if namespace == CANONICAL_NAMESPACE:
        return True
    if metadata.get("canonical_personal") or metadata.get("dhee_memory_class") == "canonical_personal":
        return True
    if str(metadata.get("canonical_kind") or "").strip().lower() in CANONICAL_MEMORY_TYPES:
        return True
    return memory_type in CANONICAL_MEMORY_TYPES


def query_allows_suppressed_class(query: str, memory_class: str) -> bool:
    query_l = (query or "").lower()
    if memory_class == "test_fixture":
        return any(term in query_l for term in ("test", "fixture", "cache test", "debug test"))
    if memory_class == "operational_event":
        return any(
            term in query_l
            for term in (
                "edited",
                "file touched",
                "tool event",
                "operational",
                "session log",
                "what changed",
                "recent edits",
            )
        )
    if memory_class == "passive_screen":
        return any(
            term in query_l
            for term in (
                "screen",
                "visible",
                "observed",
                "browser",
                "recent",
                "current",
                "latest",
                "what was i looking",
            )
        )
    return True


def memory_quality_from_record(memory: Mapping[str, Any]) -> MemoryQuality:
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), Mapping) else {}
    return classify_memory_quality(
        str(memory.get("memory") or ""),
        metadata,
        memory.get("categories") if isinstance(memory.get("categories"), list) else None,
        explicit_remember=bool(metadata.get("policy_explicit") or metadata.get("explicit_remember")),
    )


def recall_explanation(
    *,
    query: str,
    memory: Mapping[str, Any],
    score: float,
    composite_score: float,
) -> Dict[str, Any]:
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), Mapping) else {}
    memory_class = str(metadata.get("dhee_memory_class") or "")
    canonical_kind = metadata.get("canonical_kind")
    if not memory_class:
        quality = memory_quality_from_record(memory)
        memory_class = quality.memory_class
        canonical_kind = canonical_kind or quality.canonical_kind

    overlap = sorted(_tokens(query) & _tokens(str(memory.get("memory") or "")))
    confidence = _coerce_float(metadata.get("confidence"))
    if confidence is None:
        confidence = min(1.0, max(float(score or 0.0), float(composite_score or 0.0)))

    if memory_class == "canonical_personal":
        why_now = "canonical personal memory matched the request"
    elif memory_class == "passive_screen":
        why_now = "passive screen observation matched; use as evidence, not belief"
    elif memory_class == "test_fixture":
        why_now = "test fixture matched; ignore unless debugging tests"
    elif memory_class == "operational_event":
        why_now = "tool/session transport matched; use as operational evidence, not personal memory"
    elif overlap:
        why_now = "query terms overlapped stored memory"
    else:
        why_now = "semantic vector match without lexical overlap"

    return {
        "matched_memory_id": memory.get("id"),
        "overlap_terms": overlap[:8],
        "memory_class": memory_class or "ordinary",
        "memory_kind": canonical_kind or memory.get("memory_type") or metadata.get("memory_type") or "semantic",
        "confidence": round(float(confidence), 3),
        "why_now": why_now,
        "decision_risk": metadata.get("dhee_decision_risk") or "medium",
    }
