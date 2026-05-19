"""Deterministic temporal scene cards and bounded context packs.

Temporal scenes are Dhee's compact layer over noisy evidence.  They keep
provenance and searchable derivatives close at hand, while raw screenshots,
transcripts, media, and long memory bodies stay behind pointers.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


SCENE_SCHEMA_VERSION = 1
_MAX_SNIPPET_CHARS = 280
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{2,}")
_NOISE_WORDS = {
    "about", "after", "agent", "also", "and", "are", "because", "been",
    "before", "being", "build", "can", "codex", "context", "dhee", "for",
    "from", "has", "have", "into", "its", "memory", "more", "not", "now",
    "only", "repo", "should", "that", "the", "their", "then", "there",
    "this", "use", "used", "user", "was", "when", "with", "work", "will",
    "you", "your",
}
_GEM_TERMS = {
    "adapter", "api", "baseline", "behavior", "bug", "capsule", "change",
    "compatibility", "decision", "diff", "failure", "fix", "interface",
    "lesson", "migration", "privacy", "regression", "reproduce", "risk",
    "scene", "secret", "test", "token", "update",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    value = re.sub(r"\s+", " ", value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _stable_hash(payload: Any, length: int = 16) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _safe_user_key(user_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", user_id or "default")[:80] or "default"


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4)


def _tokens(text: str) -> List[str]:
    out: List[str] = []
    for match in _TOKEN_RE.findall(text or ""):
        token = match.lower()
        if token not in _NOISE_WORDS and len(token) > 2:
            out.append(token)
    return out


def _first_text(raw: Dict[str, Any]) -> str:
    for key in (
        "memory", "content", "body", "text", "summary", "digest",
        "observation", "title", "message",
    ):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    metadata = raw.get("metadata") or raw.get("meta") or {}
    if isinstance(metadata, dict):
        for key in ("text", "summary", "title", "url", "path"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _infer_modality(raw: Dict[str, Any], text: str) -> str:
    metadata = raw.get("metadata") or raw.get("meta") or {}
    if isinstance(metadata, dict):
        for key in ("modality", "media_type", "source_type"):
            value = metadata.get(key)
            if value:
                return str(value)
    source_type = str(raw.get("source_type") or raw.get("memory_type") or raw.get("kind") or "").lower()
    for candidate in ("video", "audio", "image", "ocr", "dom", "web", "screen", "artifact", "transcript"):
        if candidate in source_type:
            return candidate
    if "<html" in text.lower() or "dom" in text.lower():
        return "dom"
    return "text"


def _extract_categories(raw: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    for key in ("categories", "tags"):
        item = raw.get(key)
        if isinstance(item, list):
            values.extend(str(v) for v in item if v)
        elif isinstance(item, str):
            values.extend(part.strip() for part in item.split(",") if part.strip())
    metadata = raw.get("metadata") or raw.get("meta") or {}
    if isinstance(metadata, dict):
        item = metadata.get("categories") or metadata.get("tags")
        if isinstance(item, list):
            values.extend(str(v) for v in item if v)
        elif isinstance(item, str):
            values.extend(part.strip() for part in item.split(",") if part.strip())
    return values


@dataclass
class EvidencePointer:
    """Compact pointer to evidence plus a small searchable derivative."""

    kind: str
    ref: str
    label: str = ""
    modality: str = "text"
    user_id: str = "default"
    agent_id: str = ""
    source_app: str = ""
    source_event_id: str = ""
    run_id: str = ""
    memory_type: str = ""
    confidentiality_scope: str = "personal"
    uri: str = ""
    snippet: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_snippet: bool = True, include_private_uri: bool = True) -> Dict[str, Any]:
        data = {
            "kind": self.kind,
            "ref": self.ref,
            "label": self.label,
            "modality": self.modality,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "source_app": self.source_app,
            "source_event_id": self.source_event_id,
            "run_id": self.run_id,
            "memory_type": self.memory_type,
            "confidentiality_scope": self.confidentiality_scope,
            "metadata": dict(self.metadata or {}),
        }
        if include_private_uri and self.uri:
            data["uri"] = self.uri
        if include_snippet and self.snippet:
            data["snippet"] = self.snippet
        return data

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "EvidencePointer":
        return cls(
            kind=str(raw.get("kind") or raw.get("source_type") or "evidence"),
            ref=str(raw.get("ref") or raw.get("id") or raw.get("memory_id") or _stable_hash(raw)),
            label=str(raw.get("label") or raw.get("title") or ""),
            modality=str(raw.get("modality") or "text"),
            user_id=str(raw.get("user_id") or "default"),
            agent_id=str(raw.get("agent_id") or ""),
            source_app=str(raw.get("source_app") or ""),
            source_event_id=str(raw.get("source_event_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            memory_type=str(raw.get("memory_type") or ""),
            confidentiality_scope=str(raw.get("confidentiality_scope") or "personal"),
            uri=str(raw.get("uri") or ""),
            snippet=str(raw.get("snippet") or ""),
            metadata=dict(raw.get("metadata") or {}),
        )


@dataclass
class TemporalScene:
    """A private compact scene card compiled from many noisy evidence events."""

    id: str
    title: str
    summary: str
    topic: str = ""
    user_goal: str = ""
    action: str = ""
    outcome: str = ""
    lesson: str = ""
    entities: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    modalities: List[str] = field(default_factory=list)
    repo_refs: List[str] = field(default_factory=list)
    evidence: List[EvidencePointer] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    privacy_scope: str = "personal"
    confidence: float = 0.5
    score: float = 0.0
    tier: str = "warm"
    created_at: str = field(default_factory=_now_iso)
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_evidence_snippets: bool = True) -> Dict[str, Any]:
        return {
            "schema_version": SCENE_SCHEMA_VERSION,
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "topic": self.topic,
            "user_goal": self.user_goal,
            "action": self.action,
            "outcome": self.outcome,
            "lesson": self.lesson,
            "entities": list(self.entities),
            "tags": list(self.tags),
            "modalities": list(self.modalities),
            "repo_refs": list(self.repo_refs),
            "evidence": [
                pointer.to_dict(include_snippet=include_evidence_snippets)
                for pointer in self.evidence
            ],
            "provenance": dict(self.provenance or {}),
            "privacy_scope": self.privacy_scope,
            "confidence": round(float(self.confidence), 4),
            "score": round(float(self.score), 4),
            "tier": self.tier,
            "created_at": self.created_at,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "TemporalScene":
        evidence = [
            pointer if isinstance(pointer, EvidencePointer) else EvidencePointer.from_dict(pointer)
            for pointer in (raw.get("evidence") or [])
            if isinstance(pointer, (dict, EvidencePointer))
        ]
        return cls(
            id=str(raw.get("id") or _stable_hash(raw)),
            title=str(raw.get("title") or ""),
            summary=str(raw.get("summary") or ""),
            topic=str(raw.get("topic") or ""),
            user_goal=str(raw.get("user_goal") or ""),
            action=str(raw.get("action") or ""),
            outcome=str(raw.get("outcome") or ""),
            lesson=str(raw.get("lesson") or ""),
            entities=[str(v) for v in raw.get("entities") or []],
            tags=[str(v) for v in raw.get("tags") or []],
            modalities=[str(v) for v in raw.get("modalities") or []],
            repo_refs=[str(v) for v in raw.get("repo_refs") or []],
            evidence=evidence,
            provenance=dict(raw.get("provenance") or {}),
            privacy_scope=str(raw.get("privacy_scope") or "personal"),
            confidence=float(raw.get("confidence") or 0.5),
            score=float(raw.get("score") or 0.0),
            tier=str(raw.get("tier") or "warm"),
            created_at=str(raw.get("created_at") or _now_iso()),
            start_time=raw.get("start_time") or None,
            end_time=raw.get("end_time") or None,
            metadata=dict(raw.get("metadata") or {}),
        )

    def to_card(self, max_chars: int = 900) -> Dict[str, Any]:
        """Return a prompt-safe card with no raw evidence bodies."""

        evidence_refs = [
            {
                "kind": pointer.kind,
                "ref": pointer.ref,
                "label": pointer.label,
                "modality": pointer.modality,
                "source_app": pointer.source_app,
                "agent_id": pointer.agent_id,
                "confidentiality_scope": pointer.confidentiality_scope,
            }
            for pointer in self.evidence[:8]
        ]
        card = {
            "id": self.id,
            "title": self.title,
            "summary": _clip(self.summary, max_chars),
            "topic": self.topic,
            "lesson": _clip(self.lesson, 360),
            "tags": list(self.tags[:12]),
            "entities": list(self.entities[:12]),
            "repo_refs": list(self.repo_refs[:8]),
            "tier": self.tier,
            "score": round(float(self.score), 4),
            "confidence": round(float(self.confidence), 4),
            "evidence_refs": evidence_refs,
        }
        return card


class GemScorer:
    """Deterministic scorer that decides whether noisy evidence is a gem."""

    def score(self, text: str, pointers: Sequence[EvidencePointer]) -> float:
        terms = set(_tokens(text))
        score = 0.18
        score += min(0.22, len(terms & _GEM_TERMS) * 0.045)
        score += min(0.18, len(pointers) * 0.035)
        score += min(0.12, len({p.agent_id for p in pointers if p.agent_id}) * 0.04)
        score += min(0.12, len({p.source_app for p in pointers if p.source_app}) * 0.04)
        if any(p.modality not in ("text", "") for p in pointers):
            score += 0.06
        if any(p.confidentiality_scope in ("public", "repo", "shareable") for p in pointers):
            score += 0.04
        if len(text) > 500:
            score += 0.05
        if any(p.confidentiality_scope in ("secret", "restricted") for p in pointers):
            score -= 0.08
        return max(0.0, min(1.0, score))

    def tier(self, score: float) -> str:
        if score >= 0.72:
            return "hot"
        if score >= 0.42:
            return "warm"
        return "cold"


class SceneCompiler:
    """Compile compact scenes from memory rows, artifacts, browser captures, or agent outputs."""

    def __init__(self, scorer: Optional[GemScorer] = None) -> None:
        self.scorer = scorer or GemScorer()

    def _pointer_from_evidence(self, item: Any, default_user_id: str) -> EvidencePointer:
        raw = dataclasses.asdict(item) if dataclasses.is_dataclass(item) else item
        if not isinstance(raw, dict):
            raw = {"content": str(raw)}
        metadata = raw.get("metadata") or raw.get("meta") or {}
        if not isinstance(metadata, dict):
            metadata = {"raw_metadata": metadata}
        text = _first_text(raw)
        ref = (
            raw.get("ref") or raw.get("id") or raw.get("memory_id") or
            raw.get("source_event_id") or metadata.get("id") or metadata.get("source_event_id") or
            _stable_hash({"text": text, "metadata": metadata})
        )
        kind = str(raw.get("kind") or raw.get("memory_type") or raw.get("source_type") or "evidence")
        label = str(raw.get("title") or metadata.get("title") or _clip(text, 80))
        uri = str(raw.get("uri") or raw.get("path") or raw.get("url") or metadata.get("uri") or metadata.get("path") or metadata.get("url") or "")
        return EvidencePointer(
            kind=kind,
            ref=str(ref),
            label=label,
            modality=_infer_modality(raw, text),
            user_id=str(raw.get("user_id") or metadata.get("user_id") or default_user_id),
            agent_id=str(raw.get("agent_id") or metadata.get("agent_id") or ""),
            source_app=str(raw.get("source_app") or metadata.get("source_app") or ""),
            source_event_id=str(raw.get("source_event_id") or metadata.get("source_event_id") or ""),
            run_id=str(raw.get("run_id") or metadata.get("run_id") or ""),
            memory_type=str(raw.get("memory_type") or metadata.get("memory_type") or kind),
            confidentiality_scope=str(raw.get("confidentiality_scope") or metadata.get("confidentiality_scope") or "personal"),
            uri=uri,
            snippet=_clip(text, _MAX_SNIPPET_CHARS),
            metadata={
                key: value
                for key, value in metadata.items()
                if key not in {"text", "body", "content", "memory", "transcript", "ocr"}
            },
        )

    def compile_scene(
        self,
        evidence_items: Iterable[Any],
        *,
        user_id: str = "default",
        repo: Optional[str] = None,
        task: str = "",
        privacy_scope: str = "personal",
        title: Optional[str] = None,
    ) -> TemporalScene:
        items = list(evidence_items)
        pointers = [self._pointer_from_evidence(item, user_id) for item in items]
        if not pointers:
            raise ValueError("at least one evidence item is required to compile a scene")

        combined = " ".join(pointer.snippet for pointer in pointers if pointer.snippet)
        categories: List[str] = []
        for item in items:
            if isinstance(item, dict):
                categories.extend(_extract_categories(item))
        token_counts = Counter(_tokens(" ".join([combined, task, " ".join(categories)])))
        tags = []
        for value in categories:
            value = value.strip().lower()
            if value and value not in tags:
                tags.append(value)
        for token, _count in token_counts.most_common(16):
            if token not in tags:
                tags.append(token)
            if len(tags) >= 16:
                break
        entities = [tag for tag in tags if tag[:1].isupper()]
        if not entities:
            entities = [tag for tag in tags[:8] if tag not in _NOISE_WORDS]
        scene_title = title or _clip(task, 90) or _clip(pointers[0].label or combined, 90) or "Temporal scene"
        topic = _clip(" ".join(tags[:5]), 120) or scene_title
        modalities = sorted({pointer.modality for pointer in pointers if pointer.modality})
        repo_refs = []
        if repo:
            repo_refs.append(str(repo))
        for pointer in pointers:
            path = pointer.metadata.get("path") or pointer.metadata.get("file_path")
            if path and str(path) not in repo_refs:
                repo_refs.append(str(path))
        source_apps = sorted({p.source_app for p in pointers if p.source_app})
        agent_ids = sorted({p.agent_id for p in pointers if p.agent_id})
        source_event_ids = sorted({p.source_event_id for p in pointers if p.source_event_id})
        run_ids = sorted({p.run_id for p in pointers if p.run_id})
        memory_types = sorted({p.memory_type for p in pointers if p.memory_type})
        score = self.scorer.score(" ".join([scene_title, task, combined]), pointers)
        confidence = min(0.95, 0.45 + min(0.25, len(pointers) * 0.05) + min(0.15, len(source_apps) * 0.05))
        payload_for_id = {
            "title": scene_title,
            "task": task,
            "refs": [p.ref for p in pointers],
            "repo": repo,
        }
        scene = TemporalScene(
            id="scene_" + _stable_hash(payload_for_id, 18),
            title=scene_title,
            summary=_clip(combined, 850),
            topic=topic,
            user_goal=_clip(task, 280),
            action=_clip(task, 360) if task else "",
            outcome="Compiled reusable context from admitted evidence.",
            lesson=_clip(
                "Relevant scene for future agents: " + (task or scene_title) +
                ". Use the card first; expand evidence only by pointer when needed.",
                420,
            ),
            entities=entities[:12],
            tags=tags[:16],
            modalities=modalities or ["text"],
            repo_refs=repo_refs[:12],
            evidence=pointers,
            provenance={
                "user_id": user_id,
                "agent_ids": agent_ids,
                "source_apps": source_apps,
                "source_event_ids": source_event_ids,
                "run_ids": run_ids,
                "memory_types": memory_types,
                "evidence_count": len(pointers),
            },
            privacy_scope=privacy_scope,
            confidence=confidence,
            score=score,
            tier=self.scorer.tier(score),
            metadata={
                "source_evidence_hash": _stable_hash([p.to_dict() for p in pointers], 24),
                "task": task,
                "repo": repo or "",
                "storage_policy": "scene_card_plus_pointer_derivatives",
            },
        )
        return scene


def _normalize_sources(sources: Optional[Iterable[str]]) -> set[str]:
    if sources is None:
        return {"evidence"}
    return {str(source).strip().lower() for source in sources if str(source).strip()}


def _memory_rows(memory: Any, *, query: str, user_id: str, limit: int) -> List[Dict[str, Any]]:
    if memory is None:
        return []
    try:
        if query:
            result = memory.search(query=query, user_id=user_id, limit=limit)
        else:
            result = memory.get_all(user_id=user_id, limit=limit)
    except TypeError:
        try:
            result = memory.search(query, user_id=user_id, limit=limit)
        except Exception:
            return []
    except Exception:
        return []
    if isinstance(result, dict):
        rows = result.get("results") or result.get("memories") or []
        return [row for row in rows if isinstance(row, dict)]
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    return []


def _repo_context_rows(repo: Optional[str | os.PathLike[str]], limit: int) -> List[Dict[str, Any]]:
    if not repo:
        return []
    try:
        from dhee import repo_link

        repo_root = repo_link._resolve_repo(repo) or Path(repo).expanduser().resolve()
        entries = repo_link.list_entries(repo_root)
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for entry in entries[-max(1, int(limit)):]:
        rows.append({
            "id": entry.id,
            "kind": f"repo_context:{entry.kind}",
            "title": entry.title,
            "content": entry.content,
            "source_app": "dhee-repo-context",
            "source_event_id": entry.id,
            "agent_id": entry.created_by,
            "memory_type": entry.kind,
            "confidentiality_scope": "repo",
            "metadata": {
                "entry_id": entry.id,
                "repo": str(repo_root),
                "content_hash": entry.content_hash,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "kind": entry.kind,
                "meta": entry.meta,
            },
        })
    return rows


def _session_rows(session: Any) -> List[Dict[str, Any]]:
    if not isinstance(session, dict):
        return []
    content_parts: List[str] = []
    for key in ("task_summary", "summary", "title", "status"):
        value = session.get(key)
        if value:
            content_parts.append(f"{key}: {value}")
    for key in ("decisions", "decisions_made", "files_touched", "todos", "todos_remaining", "blockers", "test_results"):
        value = session.get(key)
        if isinstance(value, list) and value:
            content_parts.append(f"{key}: " + "; ".join(str(item) for item in value[:12]))
    content = "\n".join(content_parts).strip()
    if not content:
        return []
    return [{
        "id": session.get("id") or session.get("session_id") or _stable_hash(session, 12),
        "kind": "session_digest",
        "title": session.get("task_summary") or session.get("title") or "Session digest",
        "content": content,
        "source_app": "dhee-session",
        "source_event_id": session.get("id") or session.get("session_id") or "",
        "agent_id": session.get("agent_id") or "",
        "run_id": session.get("id") or session.get("session_id") or "",
        "memory_type": "session_digest",
        "confidentiality_scope": "personal",
        "metadata": {k: v for k, v in session.items() if k not in {"messages"}},
    }]


def _shared_task_rows(results: Any) -> List[Dict[str, Any]]:
    if isinstance(results, dict):
        rows = results.get("results") or []
    else:
        rows = results or []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        digest = row.get("digest") or row.get("summary") or row.get("content") or ""
        if not digest:
            continue
        out.append({
            "id": row.get("id") or _stable_hash(row, 12),
            "kind": row.get("packet_kind") or "shared_task_result",
            "title": row.get("tool_name") or row.get("packet_kind") or "Shared task result",
            "content": digest,
            "source_app": row.get("harness") or "dhee-shared-task",
            "source_event_id": row.get("id") or "",
            "agent_id": row.get("agent_id") or "",
            "run_id": row.get("shared_task_id") or "",
            "memory_type": "shared_task_result",
            "confidentiality_scope": "personal",
            "metadata": dict(row.get("metadata") or {}),
        })
    return out


def _artifact_rows(artifacts: Any) -> List[Dict[str, Any]]:
    rows = artifacts.get("results") if isinstance(artifacts, dict) else artifacts
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        content = row.get("summary") or row.get("text") or row.get("filename") or row.get("source_path") or ""
        if not content:
            continue
        out.append({
            "id": row.get("artifact_id") or row.get("id") or _stable_hash(row, 12),
            "kind": "artifact",
            "title": row.get("filename") or row.get("title") or "Artifact",
            "content": content,
            "source_app": "dhee-artifact",
            "source_event_id": row.get("artifact_id") or row.get("id") or "",
            "memory_type": "artifact",
            "confidentiality_scope": str(row.get("confidentiality_scope") or "personal"),
            "metadata": dict(row),
        })
    return out


def collect_scene_evidence(
    *,
    evidence: Optional[Iterable[Any]] = None,
    memory: Any = None,
    query: str = "",
    user_id: str = "default",
    repo: Optional[str | os.PathLike[str]] = None,
    session: Optional[Dict[str, Any]] = None,
    shared_task_results: Any = None,
    artifacts: Any = None,
    sources: Optional[Iterable[str]] = None,
    limit: int = 20,
) -> List[Any]:
    """Collect compact evidence derivatives from Dhee's existing surfaces.

    This is intentionally pointer/card oriented: it pulls summaries, digests,
    repo entries, and metadata identities, not raw media or unbounded logs.
    """

    selected = _normalize_sources(sources)
    rows: List[Any] = []
    if evidence and ("evidence" in selected or not selected):
        rows.extend(list(evidence))
    if "memory" in selected:
        rows.extend(_memory_rows(memory, query=query, user_id=user_id, limit=limit))
    if "repo_context" in selected or "repo" in selected:
        rows.extend(_repo_context_rows(repo, limit=limit))
    if "session" in selected or "session_digest" in selected:
        rows.extend(_session_rows(session))
    if "shared_task_results" in selected or "shared_task" in selected:
        rows.extend(_shared_task_rows(shared_task_results))
    if "artifacts" in selected or "artifact" in selected:
        rows.extend(_artifact_rows(artifacts))

    deduped: List[Any] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            key = str(row.get("id") or row.get("ref") or _stable_hash(row, 12))
        else:
            key = _stable_hash(row, 12)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= max(1, int(limit)):
            break
    return deduped


def collect_live_scene_sources(
    *,
    db: Any = None,
    repo: Optional[str | os.PathLike[str]] = None,
    user_id: str = "default",
    agent_id: str = "codex",
    limit: int = 10,
    include_session: bool = True,
    include_shared_task_results: bool = True,
    include_artifacts: bool = False,
) -> Dict[str, Any]:
    """Fetch compact live Dhee surfaces for scene compilation.

    The returned payload is shaped for :func:`collect_scene_evidence`.
    All reads are best-effort and bounded; failures return missing/empty
    fields instead of raising into MCP handlers.
    """

    out: Dict[str, Any] = {}
    repo_str = str(repo) if repo else None
    if include_session:
        session = None
        try:
            from dhee.core.kernel import get_last_session

            candidate_agents = []
            for candidate in (agent_id, "codex", "claude-code", "mcp-server"):
                if candidate and candidate not in candidate_agents:
                    candidate_agents.append(candidate)
            for candidate in candidate_agents:
                session = get_last_session(
                    agent_id=candidate,
                    repo=repo_str,
                    user_id=user_id,
                    requester_agent_id=agent_id or "codex",
                    fallback_log_recovery=True,
                )
                if session:
                    break
        except Exception:
            session = None
        if session:
            out["session"] = session

    if db is not None and include_shared_task_results:
        try:
            from dhee.core.shared_tasks import shared_task_snapshot

            out["shared_task_results"] = shared_task_snapshot(
                db,
                user_id=user_id,
                repo=repo_str,
                workspace_id=repo_str,
                limit=max(1, int(limit)),
            )
        except Exception:
            out["shared_task_results"] = {"task": None, "results": []}

    if db is not None and include_artifacts and hasattr(db, "list_artifacts"):
        try:
            out["artifacts"] = db.list_artifacts(
                user_id=user_id,
                workspace_id=repo_str,
                limit=max(1, int(limit)),
            )
        except Exception:
            out["artifacts"] = []
    return out


class SceneStore:
    """Append-only JSONL store for private scene cards."""

    def __init__(self, root: Optional[str | os.PathLike[str]] = None) -> None:
        base = (
            root or os.environ.get("DHEE_TEMPORAL_SCENE_DIR") or
            (Path(os.environ["DHEE_DATA_DIR"]) / "temporal_scenes" if os.environ.get("DHEE_DATA_DIR") else None) or
            (Path.home() / ".dhee" / "temporal_scenes")
        )
        self.root = Path(base).expanduser().resolve()

    def _path(self, user_id: str) -> Path:
        return self.root / f"{_safe_user_key(user_id)}.jsonl"

    def save(self, scene: TemporalScene) -> TemporalScene:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(str(scene.provenance.get("user_id") or "default"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(scene.to_dict(), sort_keys=True, default=str) + "\n")
        return scene

    def list(
        self,
        *,
        user_id: str = "default",
        limit: int = 50,
        include_cold: bool = True,
    ) -> List[TemporalScene]:
        path = self._path(user_id)
        if not path.exists():
            return []
        by_id: Dict[str, TemporalScene] = {}
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    scene = TemporalScene.from_dict(json.loads(line))
                except Exception:
                    continue
                if include_cold or scene.tier != "cold":
                    by_id[scene.id] = scene
        scenes = sorted(by_id.values(), key=lambda scene: (scene.score, scene.created_at), reverse=True)
        return scenes[: max(0, int(limit))]

    def search(
        self,
        query: str,
        *,
        user_id: str = "default",
        repo: Optional[str] = None,
        limit: int = 5,
        include_personal: bool = True,
    ) -> List[TemporalScene]:
        query_terms = set(_tokens(query))
        scenes = self.list(user_id=user_id, limit=500, include_cold=True)
        ranked: List[tuple[float, TemporalScene]] = []
        repo_norm = str(repo or "")
        for scene in scenes:
            if not include_personal and scene.privacy_scope == "personal":
                continue
            if repo_norm and not any(repo_norm in ref or ref in repo_norm for ref in scene.repo_refs):
                continue
            haystack = " ".join([
                scene.title, scene.summary, scene.topic, scene.lesson,
                " ".join(scene.tags), " ".join(scene.repo_refs),
            ])
            terms = set(_tokens(haystack))
            overlap = len(query_terms & terms)
            if query_terms and overlap == 0:
                continue
            rank = float(scene.score) + overlap * 0.12 + (0.05 if scene.tier == "hot" else 0)
            ranked.append((rank, scene))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [scene for _rank, scene in ranked[: max(0, int(limit))]]


class ContextPackCompiler:
    """Build hard-budget context packs from scene cards."""

    def __init__(self, store: Optional[SceneStore] = None) -> None:
        self.store = store or SceneStore()

    def build(
        self,
        query: str,
        *,
        user_id: str = "default",
        repo: Optional[str] = None,
        token_budget: int = 1200,
        limit: int = 5,
        include_personal: bool = True,
    ) -> Dict[str, Any]:
        cards: List[Dict[str, Any]] = []
        used_tokens = 0
        for scene in self.store.search(
            query,
            user_id=user_id,
            repo=repo,
            limit=limit * 3,
            include_personal=include_personal,
        ):
            remaining = max(1, int(token_budget) - used_tokens)
            card = scene.to_card(max_chars=max(80, min(900, remaining * 4)))
            card_tokens = _estimate_tokens(json.dumps(card, sort_keys=True, default=str))
            if card_tokens > remaining:
                card["summary"] = _clip(card.get("summary") or "", max(40, remaining * 2))
                card["lesson"] = _clip(card.get("lesson") or "", max(40, remaining))
                card["evidence_refs"] = list(card.get("evidence_refs") or [])[:3]
                card_tokens = _estimate_tokens(json.dumps(card, sort_keys=True, default=str))
            if card_tokens > remaining:
                card = {
                    "id": scene.id,
                    "title": _clip(scene.title, 120),
                    "summary": _clip(scene.summary, max(40, remaining * 2)),
                    "tags": scene.tags[:6],
                    "tier": scene.tier,
                    "evidence_refs": [
                        {
                            "kind": pointer.kind,
                            "ref": pointer.ref,
                            "modality": pointer.modality,
                        }
                        for pointer in scene.evidence[:2]
                    ],
                }
                card_tokens = _estimate_tokens(json.dumps(card, sort_keys=True, default=str))
            if cards and used_tokens + card_tokens > token_budget:
                continue
            if card_tokens > token_budget:
                continue
            cards.append(card)
            used_tokens += card_tokens
            if len(cards) >= limit:
                break
        return {
            "format": "dhee_context_pack.v1",
            "query": query,
            "user_id": user_id,
            "repo": repo,
            "token_budget": int(token_budget),
            "estimated_tokens": used_tokens,
            "scene_cards": cards,
            "evidence_policy": "summaries_only_raw_evidence_by_pointer",
            "raw_media_included": False,
            "full_diffs_included": False,
        }


class PromotionGate:
    """Privacy boundary between personal scenes and shareable repo capsules."""

    _LOCAL_PATH_RE = re.compile(r"(/Users/[^\s\"']+|/home/[^\s\"']+|[A-Za-z]:\\\\[^\s\"']+)")

    def sanitize_scene(self, scene: TemporalScene, *, share_scope: str = "repo") -> Dict[str, Any]:
        data = scene.to_card()
        safe_refs: List[Dict[str, Any]] = []
        for pointer in scene.evidence:
            if pointer.confidentiality_scope in {"secret", "restricted"}:
                continue
            safe = pointer.to_dict(include_snippet=False, include_private_uri=False)
            safe["confidentiality_scope"] = "redacted" if pointer.confidentiality_scope == "personal" else pointer.confidentiality_scope
            safe["label"] = self._redact_text(safe.get("label") or "")
            safe_refs.append(safe)
        data["evidence_refs"] = safe_refs[:8]
        data["privacy_scope"] = share_scope
        data["personal_context_used"] = scene.privacy_scope == "personal"
        data["summary"] = self._redact_text(data.get("summary") or "")
        data["lesson"] = self._redact_text(data.get("lesson") or "")
        return data

    def _redact_text(self, text: str) -> str:
        return self._LOCAL_PATH_RE.sub("<local-path>", text or "")


def compile_scene(
    evidence: Iterable[Any],
    *,
    user_id: str = "default",
    repo: Optional[str] = None,
    task: str = "",
    privacy_scope: str = "personal",
    title: Optional[str] = None,
    store_dir: Optional[str | os.PathLike[str]] = None,
    save: bool = True,
) -> TemporalScene:
    scene = SceneCompiler().compile_scene(
        evidence,
        user_id=user_id,
        repo=repo,
        task=task,
        privacy_scope=privacy_scope,
        title=title,
    )
    if save:
        SceneStore(store_dir).save(scene)
    return scene


def compile_scene_from_sources(
    *,
    evidence: Optional[Iterable[Any]] = None,
    memory: Any = None,
    query: str = "",
    user_id: str = "default",
    repo: Optional[str | os.PathLike[str]] = None,
    session: Optional[Dict[str, Any]] = None,
    shared_task_results: Any = None,
    artifacts: Any = None,
    sources: Optional[Iterable[str]] = None,
    limit: int = 20,
    task: str = "",
    privacy_scope: str = "personal",
    title: Optional[str] = None,
    store_dir: Optional[str | os.PathLike[str]] = None,
    save: bool = True,
) -> TemporalScene:
    collected = collect_scene_evidence(
        evidence=evidence,
        memory=memory,
        query=query,
        user_id=user_id,
        repo=repo,
        session=session,
        shared_task_results=shared_task_results,
        artifacts=artifacts,
        sources=sources,
        limit=limit,
    )
    return compile_scene(
        collected,
        user_id=user_id,
        repo=str(repo) if repo else None,
        task=task or query,
        privacy_scope=privacy_scope,
        title=title,
        store_dir=store_dir,
        save=save,
    )


def search_scenes(
    query: str,
    *,
    user_id: str = "default",
    repo: Optional[str] = None,
    limit: int = 5,
    store_dir: Optional[str | os.PathLike[str]] = None,
    include_personal: bool = True,
) -> List[TemporalScene]:
    return SceneStore(store_dir).search(
        query,
        user_id=user_id,
        repo=repo,
        limit=limit,
        include_personal=include_personal,
    )


def build_context_pack(
    query: str,
    *,
    user_id: str = "default",
    repo: Optional[str] = None,
    token_budget: int = 1200,
    limit: int = 5,
    store_dir: Optional[str | os.PathLike[str]] = None,
    include_personal: bool = True,
) -> Dict[str, Any]:
    return ContextPackCompiler(SceneStore(store_dir)).build(
        query,
        user_id=user_id,
        repo=repo,
        token_budget=token_budget,
        limit=limit,
        include_personal=include_personal,
    )
