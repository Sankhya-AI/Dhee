from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dhee.core.learnings import LearningExchange

from .capture_store import CaptureStore
from .schema import CAUSAL_SCHEMA_VERSION, RawEvent


GEM_SCHEMA_VERSION = "memory_gem.v1"

GEM_KINDS = {
    "preference",
    "decision",
    "learning",
    "task",
    "artifact",
    "context",
    "fact",
}

_PREFERENCE_TERMS = {
    "prefer",
    "preference",
    "like",
    "want",
    "style",
    "tone",
    "always",
    "never",
    "recommended",
}
_DECISION_TERMS = {
    "decided",
    "decision",
    "choose",
    "chosen",
    "instead",
    "architecture",
    "invariant",
    "law",
    "source of truth",
}
_LEARNING_TERMS = {
    "learned",
    "lesson",
    "pattern",
    "pitfall",
    "works",
    "failed",
    "fix",
    "regression",
    "test",
    "verify",
    "checkpoint",
}
_TASK_TERMS = {
    "todo",
    "task",
    "blocked",
    "next",
    "follow up",
    "implement",
    "build",
}
_ARTIFACT_TERMS = {"artifact", "file", "document", "pdf", "screenshot", "attachment"}
_PASSIVE_NOISE_PREFIXES = (
    "chotu observed useful visible screen activity",
    "edited /",
    "opened ",
    "viewed ",
)
_NOISE_KINDS = {"file_touched", "artifact_chunk", "test_fixture", "fixture"}
_PRIVACY_MAP = {
    "public": "public",
    "shareable": "public",
    "repo": "project",
    "project": "project",
    "workspace": "project",
    "connector": "connector",
    "global": "global",
    "work": "global",
    "personal": "private",
    "private": "private",
    "secret": "private",
    "restricted": "private",
}


@dataclass
class MemoryGem:
    id: str
    source_memory_id: str
    user_id: str
    kind: str
    title: str
    summary: str
    score: float
    confidence: float
    privacy_scope: str
    source_app: str = ""
    memory_type: str = ""
    categories: List[str] = field(default_factory=list)
    source_event_id: Optional[str] = None
    timestamp: str = ""
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["score"] = round(float(self.score), 4)
        data["confidence"] = round(float(self.confidence), 4)
        return data

    def to_raw_event(self) -> RawEvent:
        return RawEvent(
            id=f"gem:{self.id}",
            schema_version=CAUSAL_SCHEMA_VERSION,
            user_id=self.user_id,
            source_app="memory-gem",
            namespace="memory.gems",
            event_type=f"gem_{self.kind}",
            timestamp=self.timestamp,
            content_ref=f"memory:{self.source_memory_id}",
            content_hash=_stable_hash([self.source_memory_id, self.summary]),
            privacy_scope=self.privacy_scope,
            metadata={
                "gem_schema_version": GEM_SCHEMA_VERSION,
                "gem_id": self.id,
                "kind": self.kind,
                "score": round(float(self.score), 4),
                "confidence": round(float(self.confidence), 4),
                "title": self.title,
                "summary": self.summary,
                "source_memory_id": self.source_memory_id,
                "source_event_id": self.source_event_id,
                "source_app": self.source_app,
                "memory_type": self.memory_type,
                "categories": self.categories,
                "evidence": self.evidence,
                **self.metadata,
            },
        )


def extract_memory_gems(
    memories: Iterable[Dict[str, Any]],
    *,
    user_id: str = "default",
    limit: int = 50,
    min_score: float = 0.62,
) -> List[MemoryGem]:
    candidates: List[MemoryGem] = []
    for memory in memories:
        gem = score_memory_gem(memory, default_user_id=user_id)
        if gem and gem.score >= float(min_score):
            candidates.append(gem)
    candidates.sort(key=lambda item: (item.score, item.confidence, item.timestamp), reverse=True)
    return candidates[: max(1, int(limit or 50))]


def score_memory_gem(memory: Dict[str, Any], *, default_user_id: str = "default") -> Optional[MemoryGem]:
    text = _memory_text(memory)
    if not text:
        return None
    metadata = _dict(memory.get("metadata"))
    categories = _list(memory.get("categories"))
    source_memory_id = str(memory.get("id") or "").strip()
    if not source_memory_id:
        return None
    memory_type = str(memory.get("memory_type") or metadata.get("memory_type") or "").strip()
    source_app = str(memory.get("source_app") or metadata.get("source_app") or "").strip()
    kind_hint = str(metadata.get("kind") or "").strip().lower()
    if kind_hint in _NOISE_KINDS:
        return None

    lowered = text.lower()
    score = 0.16
    score += _float(memory.get("strength"), 0.5) * 0.18
    score += _float(memory.get("importance") or metadata.get("importance"), 0.5) * 0.18
    score += min(0.14, _float(memory.get("access_count"), 0.0) * 0.02)

    kind, kind_score = _classify_kind(text, metadata, categories, memory_type)
    score += kind_score
    if memory_type in {"task", "episodic", "semantic"}:
        score += {"task": 0.08, "episodic": 0.04, "semantic": 0.05}.get(memory_type, 0.0)
    if categories:
        score += min(0.08, len(categories) * 0.02)
    if source_app in {"gmail", "chrome", "browser", "codex", "claude-code", "chotu"}:
        score += 0.03
    if len(text) >= 80:
        score += 0.04
    if len(text) >= 280:
        score += 0.03
    if _looks_like_noise(lowered, metadata, categories):
        score -= 0.28
    if str(memory.get("tombstone") or "0") not in {"0", "False", "false", ""}:
        return None

    score = max(0.0, min(1.0, score))
    confidence = max(0.2, min(1.0, 0.48 + kind_score + _float(memory.get("strength"), 0.5) * 0.22))
    title = _title_for(text, kind)
    timestamp = str(memory.get("updated_at") or memory.get("created_at") or metadata.get("event_time") or "")
    privacy_scope = _privacy_scope(memory, metadata)
    evidence = [
        {
            "kind": "memory",
            "memory_id": source_memory_id,
            "source_event_id": memory.get("source_event_id") or metadata.get("source_event_id"),
            "source_app": source_app,
            "memory_type": memory_type,
            "categories": categories,
            "strength": memory.get("strength"),
            "importance": memory.get("importance") or metadata.get("importance"),
        }
    ]
    gem_id = "memgem_" + _stable_hash([source_memory_id, kind, title])[:16]
    return MemoryGem(
        id=gem_id,
        source_memory_id=source_memory_id,
        user_id=str(memory.get("user_id") or metadata.get("user_id") or default_user_id),
        kind=kind,
        title=title,
        summary=_clip(text, 900),
        score=score,
        confidence=confidence,
        privacy_scope=privacy_scope,
        source_app=source_app,
        memory_type=memory_type,
        categories=categories,
        source_event_id=memory.get("source_event_id") or metadata.get("source_event_id"),
        timestamp=timestamp,
        evidence=evidence,
        metadata={
            "extractor": "deterministic",
            "source_namespace": memory.get("namespace") or metadata.get("namespace"),
        },
    )


def write_gem_raw_events(
    capture_store: CaptureStore,
    gems: Sequence[MemoryGem],
    *,
    overwrite_existing: bool = False,
) -> Dict[str, Any]:
    written: List[str] = []
    skipped: List[str] = []
    for gem in gems:
        event = gem.to_raw_event()
        if capture_store.get_raw_event(event.id):
            if not overwrite_existing:
                skipped.append(event.id)
                continue
        try:
            capture_store.record_raw_event(event)
            written.append(event.id)
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                skipped.append(event.id)
                continue
            raise
    return {"written": written, "skipped_existing": skipped}


def submit_gem_learning_candidates(
    exchange: LearningExchange,
    gems: Sequence[MemoryGem],
    *,
    repo: Optional[str] = None,
    source_agent_id: str = "memory-gem-extractor",
    status: str = "candidate",
) -> Dict[str, Any]:
    submitted: List[str] = []
    rejected: List[Dict[str, Any]] = []
    for gem in gems:
        if gem.kind not in {"learning", "preference", "decision"}:
            continue
        try:
            candidate = exchange.submit(
                title=gem.title,
                body=gem.summary,
                kind=_learning_kind(gem.kind),
                source_agent_id=source_agent_id,
                source_harness="dhee",
                task_type=f"memory_gem_{gem.kind}",
                repo=repo,
                scope="personal",
                confidence=gem.confidence,
                utility=gem.score,
                evidence=gem.evidence,
                metadata={
                    "gem_id": gem.id,
                    "source_memory_id": gem.source_memory_id,
                    "privacy_scope": gem.privacy_scope,
                    "schema_version": GEM_SCHEMA_VERSION,
                },
                status=status,
                learning_id="lrn_" + gem.id[-16:],
            )
            submitted.append(candidate.id)
        except Exception as exc:
            rejected.append({"gem_id": gem.id, "reason": str(exc)})
    return {"submitted": submitted, "rejected": rejected}


def submit_projected_gem_learning_candidate(
    exchange: LearningExchange,
    projected_gem: Dict[str, Any],
    *,
    repo: Optional[str] = None,
    source_agent_id: str = "memory-gem-debug",
    status: str = "candidate",
) -> Dict[str, Any]:
    if projected_gem.get("status") != "ok":
        return {
            "submitted": [],
            "rejected": [
                {
                    "target": projected_gem.get("target"),
                    "reason": projected_gem.get("status") or "not_found",
                }
            ],
        }

    gem = _dict(projected_gem.get("gem"))
    gem_kind = str(gem.get("kind") or "").strip().lower()
    gem_id = str(gem.get("gem_id") or "").strip()
    if gem_kind not in {"learning", "preference", "decision"}:
        return {
            "submitted": [],
            "rejected": [
                {
                    "gem_id": gem_id,
                    "reason": f"gem kind {gem_kind or 'unknown'} is not promotable",
                }
            ],
        }
    if not gem_id:
        gem_id = _stable_hash([gem.get("event_id"), gem.get("title"), gem.get("summary")])[:16]

    source = _dict(projected_gem.get("source_memory"))
    evidence = list(projected_gem.get("supporting_events") or [])
    evidence.append(
        {
            "kind": "memory_gem",
            "event_id": gem.get("event_id"),
            "gem_id": gem_id,
            "source_memory_id": source.get("memory_id"),
            "content_ref": source.get("content_ref") or gem.get("content_ref"),
        }
    )
    try:
        candidate = exchange.submit(
            title=str(gem.get("title") or "").strip(),
            body=str(gem.get("summary") or "").strip(),
            kind=_learning_kind(gem_kind),
            source_agent_id=source_agent_id,
            source_harness="dhee",
            task_type=f"memory_gem_{gem_kind}",
            repo=repo,
            scope="personal",
            confidence=_float(gem.get("confidence"), 0.5),
            utility=_float(gem.get("score"), 0.0),
            evidence=evidence,
            metadata={
                "gem_id": gem_id,
                "event_id": gem.get("event_id"),
                "source_memory_id": source.get("memory_id"),
                "source_event_id": source.get("source_event_id"),
                "privacy_scope": gem.get("privacy_scope"),
                "schema_version": gem.get("schema_version"),
                "projection_version": gem.get("projection_version"),
            },
            status=status,
            learning_id="lrn_" + gem_id[-16:],
        )
        return {"submitted": [candidate.id], "rejected": [], "candidate": candidate.to_dict()}
    except Exception as exc:
        return {"submitted": [], "rejected": [{"gem_id": gem_id, "reason": str(exc)}]}


def summarize_gems(gems: Sequence[MemoryGem]) -> Dict[str, Any]:
    by_kind: Dict[str, int] = {}
    by_scope: Dict[str, int] = {}
    for gem in gems:
        by_kind[gem.kind] = by_kind.get(gem.kind, 0) + 1
        by_scope[gem.privacy_scope] = by_scope.get(gem.privacy_scope, 0) + 1
    return {
        "count": len(gems),
        "by_kind": by_kind,
        "by_privacy_scope": by_scope,
        "top": [gem.to_dict() for gem in gems[:10]],
    }


def _classify_kind(
    text: str,
    metadata: Dict[str, Any],
    categories: Sequence[str],
    memory_type: str,
) -> Tuple[str, float]:
    haystack = " ".join([text, " ".join(categories), memory_type, str(metadata.get("memory_type") or "")]).lower()
    scores = {
        "preference": _term_score(haystack, _PREFERENCE_TERMS, 0.24),
        "decision": _term_score(haystack, _DECISION_TERMS, 0.23),
        "learning": _term_score(haystack, _LEARNING_TERMS, 0.24),
        "task": _term_score(haystack, _TASK_TERMS, 0.18),
        "artifact": _term_score(haystack, _ARTIFACT_TERMS, 0.16),
    }
    if memory_type == "task":
        scores["task"] += 0.12
    if metadata.get("source_event_id"):
        scores["context"] = 0.08
    kind = max(scores, key=scores.get)
    if scores[kind] <= 0.02:
        return "fact", 0.06
    return kind if kind in GEM_KINDS else "fact", scores[kind]


def _term_score(text: str, terms: Sequence[str], cap: float) -> float:
    hits = sum(1 for term in terms if term in text)
    return min(cap, hits * (cap / 4.0))


def _looks_like_noise(text: str, metadata: Dict[str, Any], categories: Sequence[str]) -> bool:
    if any(text.startswith(prefix) for prefix in _PASSIVE_NOISE_PREFIXES):
        useful_terms = _PREFERENCE_TERMS | _DECISION_TERMS | _LEARNING_TERMS | _TASK_TERMS
        return not any(term in text for term in useful_terms)
    if str(metadata.get("kind") or "").lower() in _NOISE_KINDS:
        return True
    if "artifact_chunk" in {str(item).lower() for item in categories}:
        return True
    return False


def _memory_text(memory: Dict[str, Any]) -> str:
    return str(memory.get("memory") or memory.get("content") or memory.get("text") or "").strip()


def _title_for(text: str, kind: str) -> str:
    first = re.split(r"[\n.?!]", text.strip(), maxsplit=1)[0].strip()
    if not first:
        first = text.strip()
    return f"{kind.title()}: {_clip(first, 92)}"


def _privacy_scope(memory: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    raw = str(
        metadata.get("privacy_scope")
        or metadata.get("scope")
        or memory.get("confidentiality_scope")
        or metadata.get("confidentiality_scope")
        or "global"
    ).strip().lower()
    return _PRIVACY_MAP.get(raw, "global")


def _learning_kind(gem_kind: str) -> str:
    return {
        "preference": "policy",
        "decision": "policy",
        "learning": "heuristic",
    }.get(gem_kind, "memory")


def _clip(text: str, max_chars: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "..."


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stable_hash(parts: Any) -> str:
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()
