"""Deterministic promotion helpers for temporal fact context.

This module bridges compact scene cards, checkpoint summaries, and memory rows
into the temporal fact ledger without introducing an LLM dependency.  It keeps
promotion provenance close to every assertion so active fact context can be
retrieved later as compact prompt-safe cards.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dhee.temporal_fact_ledger import TemporalFactLedger, open_default_ledger


TEMPORAL_FACT_INTEGRATION_SCHEMA = "dhee.temporal_fact_integration.v1"
TEMPORAL_FACT_CANDIDATE_SCHEMA = "dhee.temporal_fact_candidate.v1"
TEMPORAL_FACT_CONTEXT_CARD_SCHEMA = "dhee.temporal_fact_context_card.v1"

_MAX_FACT_TEXT_CHARS = 360
_MAX_EVIDENCE_TEXT_CHARS = 220
_FACT_LIST_KEYS = ("temporal_facts", "candidate_facts", "facts")
_SCENE_TEXT_FIELDS = ("summary", "outcome", "lesson", "user_goal", "action", "title", "topic")
_CHECKPOINT_TEXT_FIELDS = (
    "summary",
    "task_summary",
    "key_decision",
    "remember_to",
    "what_worked",
    "what_failed",
    "decision",
    "status",
)
_CHECKPOINT_LIST_FIELDS = (
    "decisions",
    "decisions_made",
    "todos",
    "todos_remaining",
    "blockers",
    "test_results",
)
_MEMORY_TEXT_FIELDS = ("memory", "content", "body", "text", "summary", "digest", "observation", "title")
_IMPERATIVE_STARTS = {
    "add",
    "build",
    "create",
    "debug",
    "document",
    "fix",
    "implement",
    "keep",
    "make",
    "prefer",
    "read",
    "record",
    "return",
    "run",
    "search",
    "store",
    "test",
    "update",
    "use",
    "write",
}
_PREDICATE_ALIASES = {
    "are": "is",
    "depends on": "depends_on",
    "has": "has",
    "integrates with": "integrates_with",
    "is": "is",
    "keeps": "keeps",
    "now prefers": "prefers",
    "owns": "owns",
    "prefers": "prefers",
    "records": "records",
    "requires": "requires",
    "runs on": "runs_on",
    "stores": "stores",
    "supports": "supports",
    "uses": "uses",
}
_FACT_PATTERNS = (
    re.compile(
        r"^(?P<subject>[A-Za-z0-9_@./:' -]{2,90}?)\s+"
        r"(?P<predicate>now prefers|depends on|integrates with|runs on|prefers|requires|supports|records|stores|keeps|owns|uses|has|is|are)\s+"
        r"(?P<object>[^.;!?]{2,240})$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<subject>[A-Za-z0-9_@./:' -]{2,90}?)\s+should\s+"
        r"(?P<verb>use|prefer|keep|store|record|assert|return|receive|sync)\s+"
        r"(?P<object>[^.;!?]{2,240})$",
        re.IGNORECASE,
    ),
)


def _compact(value: Any, limit: int = _MAX_FACT_TEXT_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _canonical(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _stable_hash(payload: Any, length: int = 16) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if dataclasses.is_dataclass(value):
        return asdict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, dict):
            return dict(result)
    return {}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    return [value]


def _string_list(value: Any) -> List[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _dedupe(values: Iterable[Any]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _metadata(raw: Dict[str, Any]) -> Dict[str, Any]:
    metadata = raw.get("metadata") or raw.get("meta") or {}
    return dict(metadata) if isinstance(metadata, dict) else {}


def _privacy_scope(raw: Dict[str, Any], default: str = "personal") -> str:
    metadata = _metadata(raw)
    return str(
        raw.get("privacy_scope")
        or raw.get("confidentiality_scope")
        or metadata.get("privacy_scope")
        or metadata.get("confidentiality_scope")
        or default
    )


def _observed_at(raw: Dict[str, Any]) -> Optional[str]:
    metadata = _metadata(raw)
    for key in ("observed_at", "end_time", "start_time", "created_at", "updated_at", "timestamp"):
        value = raw.get(key) or metadata.get(key)
        if value:
            return str(value)
    return None


def _sentence_parts(text: Any) -> List[str]:
    normalized = str(text or "").replace("\r", "\n")
    normalized = re.sub(r"^\s*[-*]\s+", "", normalized, flags=re.MULTILINE)
    pieces: List[str] = []
    for line in normalized.splitlines():
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?])\s+|;\s+", line):
            part = _compact(part.strip(" -\t\n\r\"'"), _MAX_FACT_TEXT_CHARS)
            part = part.rstrip(".!?")
            if part:
                pieces.append(part)
    return pieces


def _valid_fact_parts(subject: str, predicate: str, object_value: str) -> bool:
    subject_norm = _canonical(subject)
    object_norm = _canonical(object_value)
    if not subject_norm or not object_norm:
        return False
    if len(subject_norm) < 2 or len(object_norm) < 2:
        return False
    if len(subject_norm.split()) > 9:
        return False
    first_subject_token = subject_norm.split()[0]
    if first_subject_token in _IMPERATIVE_STARTS:
        return False
    if subject_norm in {"i", "we", "you", "they", "it", "this", "that"}:
        return False
    if object_norm.startswith(("the ", "a ", "an ")):
        object_norm = object_norm.split(" ", 1)[1]
    return bool(predicate and object_norm)


def _infer_fact_from_text(text: str) -> Optional[Tuple[str, str, str, str]]:
    statement = _compact(text).strip()
    if not statement:
        return None
    for pattern in _FACT_PATTERNS:
        match = pattern.match(statement)
        if not match:
            continue
        subject = _compact(match.group("subject").strip(" :,-"), 120)
        if "predicate" in match.groupdict():
            predicate = _PREDICATE_ALIASES.get(_canonical(match.group("predicate")), _canonical(match.group("predicate")).replace(" ", "_"))
        else:
            predicate = "should_" + _canonical(match.group("verb")).replace(" ", "_")
        object_value = _compact(match.group("object").strip(" :,-"), 240)
        if _valid_fact_parts(subject, predicate, object_value):
            return statement, subject, predicate, object_value
    return None


def _iter_fact_items(raw: Dict[str, Any]) -> Iterable[Any]:
    for key in _FACT_LIST_KEYS:
        for item in _as_list(raw.get(key)):
            yield item
    metadata = _metadata(raw)
    for key in _FACT_LIST_KEYS:
        for item in _as_list(metadata.get(key)):
            yield item


@dataclass
class TemporalFactCandidate:
    """A deterministic candidate fact ready for ledger assertion."""

    fact_text: str
    subject: str
    predicate: str
    object: str
    user_id: str = "default"
    namespace: str = "default"
    confidence: float = 0.7
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    observed_at: Optional[str] = None
    source_scene: str = ""
    source_event_ids: List[str] = field(default_factory=list)
    source_memory_ids: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    privacy_scope: str = "personal"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return "tfc_" + _stable_hash(
            {
                "fact_text": _canonical(self.fact_text),
                "subject": _canonical(self.subject),
                "predicate": _canonical(self.predicate),
                "object": _canonical(self.object),
                "source_scene": self.source_scene,
                "source_event_ids": self.source_event_ids,
                "source_memory_ids": self.source_memory_ids,
            },
            18,
        )

    @property
    def key(self) -> Tuple[str, str, str, str, str]:
        return (
            self.user_id,
            self.namespace,
            _canonical(self.subject),
            _canonical(self.predicate),
            _canonical(self.object),
        )

    def merge_provenance(self, other: "TemporalFactCandidate") -> None:
        self.source_event_ids = _dedupe([*self.source_event_ids, *other.source_event_ids])
        self.source_memory_ids = _dedupe([*self.source_memory_ids, *other.source_memory_ids])
        if not self.source_scene and other.source_scene:
            self.source_scene = other.source_scene
        self.evidence = _merge_evidence([*self.evidence, *other.evidence])
        self.confidence = max(float(self.confidence), float(other.confidence))
        sources = _dedupe(_string_list(self.metadata.get("sources")) + _string_list(other.metadata.get("sources")))
        if sources:
            self.metadata["sources"] = sources

    def to_assert_kwargs(self, *, actor_id: str = "", invalidate_conflicts: bool = True) -> Dict[str, Any]:
        return {
            "fact_text": self.fact_text,
            "user_id": self.user_id,
            "namespace": self.namespace,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "observed_at": self.observed_at,
            "confidence": self.confidence,
            "source_scene": self.source_scene,
            "source_event_ids": self.source_event_ids,
            "source_memory_ids": self.source_memory_ids,
            "evidence": self.evidence,
            "privacy_scope": self.privacy_scope,
            "metadata": dict(self.metadata),
            "invalidate_conflicts": invalidate_conflicts,
            "actor_id": actor_id,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": TEMPORAL_FACT_CANDIDATE_SCHEMA,
            "id": self.id,
            "fact_text": self.fact_text,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "user_id": self.user_id,
            "namespace": self.namespace,
            "confidence": round(float(self.confidence), 4),
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "observed_at": self.observed_at,
            "source_scene": self.source_scene,
            "source_event_ids": list(self.source_event_ids),
            "source_memory_ids": list(self.source_memory_ids),
            "evidence": [dict(item) for item in self.evidence],
            "privacy_scope": self.privacy_scope,
            "metadata": dict(self.metadata),
        }


def _merge_evidence(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _stable_hash(item, 16)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(item))
    return out


def _dedupe_candidates(candidates: Iterable[TemporalFactCandidate]) -> List[TemporalFactCandidate]:
    by_key: Dict[Tuple[str, str, str, str, str], TemporalFactCandidate] = {}
    for candidate in candidates:
        if not candidate.fact_text.strip():
            continue
        existing = by_key.get(candidate.key)
        if existing is None:
            by_key[candidate.key] = candidate
        else:
            existing.merge_provenance(candidate)
    return list(by_key.values())


def _candidate_from_parts(
    *,
    fact_text: str,
    subject: str,
    predicate: str,
    object_value: str,
    user_id: str,
    namespace: str,
    confidence: float,
    observed_at: Optional[str],
    valid_from: Optional[str],
    valid_to: Optional[str],
    source_scene: str,
    source_event_ids: Sequence[str],
    source_memory_ids: Sequence[str],
    evidence: Sequence[Dict[str, Any]],
    privacy_scope: str,
    metadata: Dict[str, Any],
) -> Optional[TemporalFactCandidate]:
    text = _compact(fact_text)
    subject_value = _compact(subject, 120)
    predicate_value = _canonical(predicate).replace(" ", "_")
    object_text = _compact(object_value, 240)
    if not text or not _valid_fact_parts(subject_value, predicate_value, object_text):
        return None
    return TemporalFactCandidate(
        fact_text=text,
        subject=subject_value,
        predicate=predicate_value,
        object=object_text,
        user_id=user_id,
        namespace=namespace,
        confidence=confidence,
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=observed_at,
        source_scene=source_scene,
        source_event_ids=_dedupe(source_event_ids),
        source_memory_ids=_dedupe(source_memory_ids),
        evidence=_merge_evidence(list(evidence)),
        privacy_scope=privacy_scope,
        metadata=dict(metadata),
    )


def _structured_candidates(
    raw: Dict[str, Any],
    *,
    user_id: str,
    namespace: str,
    confidence: float,
    observed_at: Optional[str],
    source_scene: str,
    source_event_ids: Sequence[str],
    source_memory_ids: Sequence[str],
    privacy_scope: str,
    origin: str,
    ref: str,
) -> List[TemporalFactCandidate]:
    candidates: List[TemporalFactCandidate] = []
    for index, item in enumerate(_iter_fact_items(raw)):
        item_raw = _as_dict(item) if not isinstance(item, str) else {"fact_text": item}
        if not item_raw:
            continue
        fact_text = (
            item_raw.get("fact_text")
            or item_raw.get("text")
            or item_raw.get("statement")
            or item_raw.get("summary")
            or ""
        )
        subject = str(item_raw.get("subject") or "").strip()
        predicate = str(item_raw.get("predicate") or "").strip()
        object_value = str(item_raw.get("object") or item_raw.get("object_value") or item_raw.get("value") or "").strip()
        inferred = _infer_fact_from_text(str(fact_text)) if fact_text else None
        if inferred:
            inferred_text, inferred_subject, inferred_predicate, inferred_object = inferred
            fact_text = str(fact_text or inferred_text)
            subject = subject or inferred_subject
            predicate = predicate or inferred_predicate
            object_value = object_value or inferred_object
        if not fact_text and subject and predicate and object_value:
            fact_text = f"{subject} {predicate.replace('_', ' ')} {object_value}."
        evidence = [
            {
                "kind": origin,
                "ref": ref,
                "field": "structured_fact",
                "index": index,
                "quote": _compact(fact_text, _MAX_EVIDENCE_TEXT_CHARS),
            }
        ]
        metadata = {
            "extractor": "deterministic_structured_fact",
            "origin": origin,
            "source_ref": ref,
            "sources": [origin],
        }
        item_metadata = item_raw.get("metadata") or {}
        if isinstance(item_metadata, dict):
            metadata.update({f"fact_{key}": value for key, value in item_metadata.items() if key not in {"body", "content"}})
        candidate = _candidate_from_parts(
            fact_text=str(fact_text or ""),
            subject=subject,
            predicate=predicate,
            object_value=object_value,
            user_id=str(item_raw.get("user_id") or user_id),
            namespace=str(item_raw.get("namespace") or namespace),
            confidence=_float(item_raw.get("confidence"), confidence),
            observed_at=str(item_raw.get("observed_at") or observed_at or "") or None,
            valid_from=item_raw.get("valid_from"),
            valid_to=item_raw.get("valid_to"),
            source_scene=str(item_raw.get("source_scene") or source_scene),
            source_event_ids=_dedupe([*source_event_ids, *_string_list(item_raw.get("source_event_ids"))]),
            source_memory_ids=_dedupe([*source_memory_ids, *_string_list(item_raw.get("source_memory_ids"))]),
            evidence=evidence,
            privacy_scope=str(item_raw.get("privacy_scope") or privacy_scope),
            metadata=metadata,
        )
        if candidate:
            candidates.append(candidate)
    return candidates


def _text_candidates(
    *,
    texts: Iterable[Tuple[str, Any]],
    user_id: str,
    namespace: str,
    confidence: float,
    observed_at: Optional[str],
    source_scene: str,
    source_event_ids: Sequence[str],
    source_memory_ids: Sequence[str],
    privacy_scope: str,
    origin: str,
    ref: str,
) -> List[TemporalFactCandidate]:
    candidates: List[TemporalFactCandidate] = []
    for field_name, text in texts:
        for sentence in _sentence_parts(text):
            inferred = _infer_fact_from_text(sentence)
            if not inferred:
                continue
            fact_text, subject, predicate, object_value = inferred
            evidence = [
                {
                    "kind": origin,
                    "ref": ref,
                    "field": field_name,
                    "quote": _compact(sentence, _MAX_EVIDENCE_TEXT_CHARS),
                }
            ]
            candidate = _candidate_from_parts(
                fact_text=fact_text,
                subject=subject,
                predicate=predicate,
                object_value=object_value,
                user_id=user_id,
                namespace=namespace,
                confidence=confidence,
                observed_at=observed_at,
                valid_from=None,
                valid_to=None,
                source_scene=source_scene,
                source_event_ids=source_event_ids,
                source_memory_ids=source_memory_ids,
                evidence=evidence,
                privacy_scope=privacy_scope,
                metadata={
                    "extractor": "deterministic_sentence_fact",
                    "origin": origin,
                    "source_ref": ref,
                    "source_field": field_name,
                    "sources": [origin],
                },
            )
            if candidate:
                candidates.append(candidate)
    return candidates


def _scene_provenance(raw: Dict[str, Any]) -> Tuple[str, List[str], List[str]]:
    scene_id = str(raw.get("id") or raw.get("scene_id") or "")
    provenance = raw.get("provenance") or {}
    provenance = provenance if isinstance(provenance, dict) else {}
    evidence_refs = [
        ref for ref in _as_list(raw.get("evidence_refs") or raw.get("evidence")) if isinstance(ref, dict)
    ]
    source_event_ids: List[Any] = []
    source_event_ids.extend(_string_list(raw.get("source_event_ids")))
    source_event_ids.extend(_string_list(provenance.get("source_event_ids")))
    source_memory_ids: List[Any] = []
    source_memory_ids.extend(_string_list(raw.get("source_memory_ids")))
    source_memory_ids.extend(_string_list(raw.get("memory_ids")))
    source_memory_ids.extend(_string_list(provenance.get("source_memory_ids")))
    source_memory_ids.extend(_string_list(provenance.get("memory_ids")))
    for pointer in evidence_refs:
        source_event_ids.extend(_string_list(pointer.get("source_event_id")))
        source_event_ids.extend(_string_list(pointer.get("source_event_ids")))
        ref = pointer.get("memory_id") or pointer.get("ref")
        if ref:
            source_memory_ids.append(ref)
    return scene_id, _dedupe(source_event_ids), _dedupe(source_memory_ids)


def extract_candidate_facts_from_scene_card(
    scene_card: Any,
    *,
    user_id: str = "default",
    namespace: str = "default",
    max_candidates: int = 12,
) -> List[TemporalFactCandidate]:
    """Extract candidate facts from a compact temporal scene card."""

    raw = _as_dict(scene_card)
    if not raw:
        return []
    scene_id, source_event_ids, source_memory_ids = _scene_provenance(raw)
    ref = scene_id or _stable_hash(raw, 12)
    confidence = _float(raw.get("confidence"), 0.72)
    observed_at = _observed_at(raw)
    privacy_scope = _privacy_scope(raw)
    texts = [(key, raw.get(key)) for key in _SCENE_TEXT_FIELDS if raw.get(key)]
    candidates = [
        *_structured_candidates(
            raw,
            user_id=user_id,
            namespace=namespace,
            confidence=max(confidence, 0.78),
            observed_at=observed_at,
            source_scene=scene_id,
            source_event_ids=source_event_ids,
            source_memory_ids=source_memory_ids,
            privacy_scope=privacy_scope,
            origin="scene_card",
            ref=ref,
        ),
        *_text_candidates(
            texts=texts,
            user_id=user_id,
            namespace=namespace,
            confidence=max(0.55, confidence - 0.06),
            observed_at=observed_at,
            source_scene=scene_id,
            source_event_ids=source_event_ids,
            source_memory_ids=source_memory_ids,
            privacy_scope=privacy_scope,
            origin="scene_card",
            ref=ref,
        ),
    ]
    return _dedupe_candidates(candidates)[: max(1, int(max_candidates or 12))]


def _checkpoint_provenance(raw: Dict[str, Any]) -> Tuple[str, List[str], List[str]]:
    source_scene = str(raw.get("source_scene") or raw.get("scene_id") or "")
    checkpoint_id = str(raw.get("id") or raw.get("checkpoint_id") or raw.get("session_id") or "")
    source_event_ids: List[Any] = []
    source_event_ids.extend(_string_list(raw.get("source_event_ids")))
    for key in ("source_event_id", "checkpoint_id", "session_id", "id", "run_id"):
        if raw.get(key):
            source_event_ids.append(raw.get(key))
    source_memory_ids: List[Any] = []
    source_memory_ids.extend(_string_list(raw.get("source_memory_ids")))
    source_memory_ids.extend(_string_list(raw.get("memory_ids")))
    return source_scene or (f"checkpoint:{checkpoint_id}" if checkpoint_id else ""), _dedupe(source_event_ids), _dedupe(source_memory_ids)


def extract_candidate_facts_from_checkpoint(
    checkpoint: Any,
    *,
    user_id: str = "default",
    namespace: str = "default",
    max_candidates: int = 12,
) -> List[TemporalFactCandidate]:
    """Extract candidate facts from a checkpoint/session summary payload."""

    raw = _as_dict(checkpoint)
    if not raw:
        return []
    source_scene, source_event_ids, source_memory_ids = _checkpoint_provenance(raw)
    ref = str(raw.get("id") or raw.get("checkpoint_id") or raw.get("session_id") or _stable_hash(raw, 12))
    observed_at = _observed_at(raw)
    privacy_scope = _privacy_scope(raw)
    texts: List[Tuple[str, Any]] = [(key, raw.get(key)) for key in _CHECKPOINT_TEXT_FIELDS if raw.get(key)]
    for key in _CHECKPOINT_LIST_FIELDS:
        for index, value in enumerate(_as_list(raw.get(key))):
            if value:
                texts.append((f"{key}[{index}]", value))
    candidates = [
        *_structured_candidates(
            raw,
            user_id=user_id,
            namespace=namespace,
            confidence=0.8,
            observed_at=observed_at,
            source_scene=source_scene,
            source_event_ids=source_event_ids,
            source_memory_ids=source_memory_ids,
            privacy_scope=privacy_scope,
            origin="checkpoint",
            ref=ref,
        ),
        *_text_candidates(
            texts=texts,
            user_id=user_id,
            namespace=namespace,
            confidence=0.66,
            observed_at=observed_at,
            source_scene=source_scene,
            source_event_ids=source_event_ids,
            source_memory_ids=source_memory_ids,
            privacy_scope=privacy_scope,
            origin="checkpoint",
            ref=ref,
        ),
    ]
    return _dedupe_candidates(candidates)[: max(1, int(max_candidates or 12))]


def _memory_provenance(raw: Dict[str, Any]) -> Tuple[str, List[str], List[str]]:
    metadata = _metadata(raw)
    source_scene = str(
        raw.get("source_scene")
        or raw.get("scene_id")
        or raw.get("current_scene_id")
        or metadata.get("source_scene")
        or metadata.get("scene_id")
        or ""
    )
    source_event_ids: List[Any] = []
    source_event_ids.extend(_string_list(raw.get("source_event_ids")))
    source_event_ids.extend(_string_list(metadata.get("source_event_ids")))
    for key in ("source_event_id", "event_id"):
        if raw.get(key):
            source_event_ids.append(raw.get(key))
        if metadata.get(key):
            source_event_ids.append(metadata.get(key))
    source_memory_ids = _string_list(raw.get("source_memory_ids")) + _string_list(metadata.get("source_memory_ids"))
    memory_id = raw.get("id") or raw.get("memory_id") or metadata.get("id") or metadata.get("memory_id")
    if memory_id:
        source_memory_ids.insert(0, memory_id)
    return source_scene, _dedupe(source_event_ids), _dedupe(source_memory_ids)


def extract_candidate_facts_from_memory_row(
    memory_row: Any,
    *,
    user_id: str = "default",
    namespace: str = "default",
    max_candidates: int = 12,
) -> List[TemporalFactCandidate]:
    """Extract candidate facts from a memory row or memory-like dictionary."""

    raw = _as_dict(memory_row)
    if not raw:
        return []
    source_scene, source_event_ids, source_memory_ids = _memory_provenance(raw)
    ref = str(raw.get("id") or raw.get("memory_id") or _stable_hash(raw, 12))
    observed_at = _observed_at(raw)
    privacy_scope = _privacy_scope(raw)
    texts = [(key, raw.get(key)) for key in _MEMORY_TEXT_FIELDS if raw.get(key)]
    candidates = [
        *_structured_candidates(
            raw,
            user_id=str(raw.get("user_id") or user_id),
            namespace=str(raw.get("namespace") or namespace),
            confidence=0.78,
            observed_at=observed_at,
            source_scene=source_scene,
            source_event_ids=source_event_ids,
            source_memory_ids=source_memory_ids,
            privacy_scope=privacy_scope,
            origin="memory_row",
            ref=ref,
        ),
        *_text_candidates(
            texts=texts,
            user_id=str(raw.get("user_id") or user_id),
            namespace=str(raw.get("namespace") or namespace),
            confidence=0.62,
            observed_at=observed_at,
            source_scene=source_scene,
            source_event_ids=source_event_ids,
            source_memory_ids=source_memory_ids,
            privacy_scope=privacy_scope,
            origin="memory_row",
            ref=ref,
        ),
    ]
    return _dedupe_candidates(candidates)[: max(1, int(max_candidates or 12))]


def _payloads(value: Any, keys: Sequence[str]) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        for key in keys:
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
        return [value]
    return _as_list(value)


def collect_candidate_facts(
    *,
    scene_cards: Any = None,
    checkpoints: Any = None,
    memory_rows: Any = None,
    user_id: str = "default",
    namespace: str = "default",
    max_candidates_per_source: int = 12,
) -> List[TemporalFactCandidate]:
    """Collect and deduplicate fact candidates from supported context shapes."""

    candidates: List[TemporalFactCandidate] = []
    for scene_card in _payloads(scene_cards, ("results", "scenes", "scene_cards")):
        candidates.extend(
            extract_candidate_facts_from_scene_card(
                scene_card,
                user_id=user_id,
                namespace=namespace,
                max_candidates=max_candidates_per_source,
            )
        )
    for checkpoint in _payloads(checkpoints, ("results", "checkpoints", "sessions")):
        candidates.extend(
            extract_candidate_facts_from_checkpoint(
                checkpoint,
                user_id=user_id,
                namespace=namespace,
                max_candidates=max_candidates_per_source,
            )
        )
    for memory_row in _payloads(memory_rows, ("results", "memories", "memory_rows")):
        candidates.extend(
            extract_candidate_facts_from_memory_row(
                memory_row,
                user_id=user_id,
                namespace=namespace,
                max_candidates=max_candidates_per_source,
            )
        )
    return _dedupe_candidates(candidates)


def _open_ledger(ledger: Optional[TemporalFactLedger], db_path: Optional[str]) -> Tuple[TemporalFactLedger, bool]:
    if ledger is not None:
        return ledger, False
    return open_default_ledger(db_path), True


def _fact_context_card(fact: Dict[str, Any]) -> Dict[str, Any]:
    evidence = [item for item in fact.get("evidence") or [] if isinstance(item, dict)]
    evidence_refs = [
        {
            "kind": item.get("kind") or "evidence",
            "ref": item.get("ref") or "",
            "field": item.get("field") or "",
            "quote": _compact(item.get("quote") or "", _MAX_EVIDENCE_TEXT_CHARS),
        }
        for item in evidence[:6]
    ]
    return {
        "format": TEMPORAL_FACT_CONTEXT_CARD_SCHEMA,
        "id": fact.get("id"),
        "fact_text": fact.get("fact_text"),
        "subject": fact.get("subject"),
        "predicate": fact.get("predicate"),
        "object": fact.get("object"),
        "confidence": fact.get("confidence"),
        "score": fact.get("score"),
        "valid_from": fact.get("valid_from"),
        "valid_to": fact.get("valid_to"),
        "observed_at": fact.get("observed_at"),
        "status": fact.get("status"),
        "active": bool(fact.get("active")),
        "source_scene": fact.get("source_scene") or "",
        "source_event_ids": _string_list(fact.get("source_event_ids")),
        "source_memory_ids": _string_list(fact.get("source_memory_ids")),
        "provenance": {
            "source_scene": fact.get("source_scene") or "",
            "source_event_ids": _string_list(fact.get("source_event_ids")),
            "source_memory_ids": _string_list(fact.get("source_memory_ids")),
            "evidence_count": len(evidence),
            "evidence_refs": evidence_refs,
        },
        "metadata": {
            key: value
            for key, value in dict(fact.get("metadata") or {}).items()
            if key in {"extractor", "origin", "source_ref", "source_field", "sources"}
        },
    }


def active_fact_context_cards(
    *,
    ledger: Optional[TemporalFactLedger] = None,
    db_path: Optional[str] = None,
    query: str = "",
    user_id: str = "default",
    namespace: Optional[str] = None,
    as_of: Optional[str] = None,
    privacy_scope: Optional[str] = None,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """Return active temporal facts as compact context cards."""

    active_ledger, should_close = _open_ledger(ledger, db_path)
    try:
        result = active_ledger.search(
            query,
            user_id=user_id,
            namespace=namespace,
            active_only=True,
            as_of=as_of,
            privacy_scope=privacy_scope,
            limit=limit,
        )
        return [_fact_context_card(fact) for fact in result.get("results", [])]
    finally:
        if should_close:
            active_ledger.close()


def assert_candidate_fact(
    candidate: TemporalFactCandidate,
    *,
    ledger: Optional[TemporalFactLedger] = None,
    db_path: Optional[str] = None,
    actor_id: str = "temporal_fact_integration",
    invalidate_conflicts: bool = True,
) -> Dict[str, Any]:
    """Assert one candidate into the temporal fact ledger."""

    active_ledger, should_close = _open_ledger(ledger, db_path)
    try:
        return active_ledger.assert_fact(
            **candidate.to_assert_kwargs(
                actor_id=actor_id,
                invalidate_conflicts=invalidate_conflicts,
            )
        )
    finally:
        if should_close:
            active_ledger.close()


def promote_temporal_facts(
    *,
    ledger: Optional[TemporalFactLedger] = None,
    db_path: Optional[str] = None,
    scene_cards: Any = None,
    checkpoints: Any = None,
    memory_rows: Any = None,
    user_id: str = "default",
    namespace: str = "default",
    query: str = "",
    limit: int = 12,
    actor_id: str = "temporal_fact_integration",
    invalidate_conflicts: bool = True,
    max_candidates_per_source: int = 12,
) -> Dict[str, Any]:
    """Extract, assert, and return active temporal fact context cards."""

    candidates = collect_candidate_facts(
        scene_cards=scene_cards,
        checkpoints=checkpoints,
        memory_rows=memory_rows,
        user_id=user_id,
        namespace=namespace,
        max_candidates_per_source=max_candidates_per_source,
    )
    active_ledger, should_close = _open_ledger(ledger, db_path)
    assertions: List[Dict[str, Any]] = []
    try:
        for candidate in candidates:
            assertions.append(
                active_ledger.assert_fact(
                    **candidate.to_assert_kwargs(
                        actor_id=actor_id,
                        invalidate_conflicts=invalidate_conflicts,
                    )
                )
            )
        cards = active_fact_context_cards(
            ledger=active_ledger,
            query=query,
            user_id=user_id,
            namespace=namespace,
            limit=limit,
        )
    finally:
        if should_close:
            active_ledger.close()
    return {
        "format": TEMPORAL_FACT_INTEGRATION_SCHEMA,
        "schema_version": TEMPORAL_FACT_INTEGRATION_SCHEMA,
        "user_id": user_id,
        "namespace": namespace,
        "candidate_count": len(candidates),
        "asserted_count": len(assertions),
        "reused_count": sum(1 for item in assertions if item.get("reused")),
        "invalidated_count": sum(len(item.get("invalidated") or []) for item in assertions),
        "candidates": [candidate.to_dict() for candidate in candidates],
        "assertions": assertions,
        "active_fact_context_cards": cards,
    }


def promote_scene_facts(scene_cards: Any, **kwargs: Any) -> Dict[str, Any]:
    """Promote temporal fact candidates from scene card payloads."""

    return promote_temporal_facts(scene_cards=scene_cards, **kwargs)


def promote_checkpoint_facts(checkpoints: Any, **kwargs: Any) -> Dict[str, Any]:
    """Promote temporal fact candidates from checkpoint/session payloads."""

    return promote_temporal_facts(checkpoints=checkpoints, **kwargs)


def promote_memory_row_facts(memory_rows: Any, **kwargs: Any) -> Dict[str, Any]:
    """Promote temporal fact candidates from memory row payloads."""

    return promote_temporal_facts(memory_rows=memory_rows, **kwargs)

