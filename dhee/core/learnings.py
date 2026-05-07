"""Shared learning exchange for Dhee-enabled agents.

This module stores auditable learning candidates separately from ordinary
memories. Only promoted learnings are returned for context injection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from dhee.configs.base import _dhee_data_dir


LEARNING_STATUSES = {"candidate", "promoted", "rejected", "archived"}
LEARNING_SCOPES = {"personal", "repo", "workspace"}
LEARNING_KINDS = {"skill", "heuristic", "policy", "contrast", "memory", "workflow", "playbook"}

_PROMPT_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "reveal the system prompt",
    "print the system prompt",
    "developer message",
    "bypass safety",
    "jailbreak",
)


class LearningError(ValueError):
    """Base class for learning exchange validation errors."""


class PromotionError(LearningError):
    """Raised when a learning cannot be promoted under the current policy."""


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return "lrn_" + uuid.uuid4().hex[:16]


def _clamp(value: Optional[float], default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _normalise_kind(kind: Optional[str]) -> str:
    value = str(kind or "heuristic").strip().lower()
    return value if value in LEARNING_KINDS else "heuristic"


def _normalise_status(status: Optional[str]) -> str:
    value = str(status or "candidate").strip().lower()
    if value not in LEARNING_STATUSES:
        raise LearningError(f"unknown learning status: {status}")
    return value


def _normalise_scope(scope: Optional[str]) -> str:
    value = str(scope or "personal").strip().lower()
    if value not in LEARNING_SCOPES:
        raise LearningError(f"unknown learning scope: {scope}")
    return value


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _has_prompt_injection(text: str) -> bool:
    haystack = " ".join(str(text or "").lower().split())
    return any(pattern in haystack for pattern in _PROMPT_INJECTION_PATTERNS)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", str(text or "").lower())


@dataclass
class LearningCandidate:
    """Canonical transferable learning object."""

    id: str
    kind: str
    title: str
    body: str
    source_agent_id: str = "unknown"
    source_harness: str = "unknown"
    task_type: Optional[str] = None
    repo: Optional[str] = None
    scope: str = "personal"
    confidence: float = 0.5
    utility: float = 0.0
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "candidate"
    reuse_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    promoted_at: Optional[float] = None
    rejected_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.kind = _normalise_kind(self.kind)
        self.status = _normalise_status(self.status)
        self.scope = _normalise_scope(self.scope)
        self.confidence = _clamp(self.confidence, 0.5)
        self.utility = _clamp(self.utility, 0.0)
        self.title = str(self.title or "").strip()
        self.body = str(self.body or "").strip()
        self.source_agent_id = str(self.source_agent_id or "unknown")
        self.source_harness = str(self.source_harness or "unknown")
        self.reuse_count = max(0, int(self.reuse_count or 0))
        self.success_count = max(0, int(self.success_count or 0))
        self.failure_count = max(0, int(self.failure_count or 0))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LearningCandidate":
        allowed = set(cls.__dataclass_fields__.keys())
        cleaned = {k: v for k, v in dict(data or {}).items() if k in allowed}
        if not cleaned.get("id"):
            cleaned["id"] = _new_id()
        return cls(**cleaned)

    def compact(self, max_body_chars: int = 500) -> Dict[str, Any]:
        body = self.body
        if len(body) > max_body_chars:
            body = body[: max_body_chars - 1].rstrip() + "..."
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": body,
            "source_agent_id": self.source_agent_id,
            "source_harness": self.source_harness,
            "task_type": self.task_type,
            "repo": self.repo,
            "scope": self.scope,
            "confidence": round(self.confidence, 3),
            "utility": round(self.utility, 3),
            "status": self.status,
        }


class LearningExchange:
    """Local learning exchange with gated promotion and repo export."""

    def __init__(self, data_dir: Optional[Union[os.PathLike, str]] = None):
        root = Path(data_dir) if data_dir is not None else Path(_dhee_data_dir()) / "learnings"
        self.data_dir = root.expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "learnings.jsonl"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def list(self, status: Optional[str] = None) -> List[LearningCandidate]:
        status_filter = _normalise_status(status) if status else None
        rows: List[LearningCandidate] = []
        if not self.path.exists():
            return rows
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    candidate = LearningCandidate.from_dict(json.loads(line))
                except Exception:
                    continue
                if status_filter and candidate.status != status_filter:
                    continue
                rows.append(candidate)
        return rows

    def get(self, learning_id: str) -> Optional[LearningCandidate]:
        lid = str(learning_id or "").strip()
        for item in self.list():
            if item.id == lid:
                return item
        return None

    def _write_all(self, rows: Sequence[LearningCandidate]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row.to_dict(), sort_keys=True) + "\n")
        os.replace(str(tmp), str(self.path))

    def _upsert(self, candidate: LearningCandidate) -> LearningCandidate:
        rows = self.list()
        out: List[LearningCandidate] = []
        replaced = False
        candidate.updated_at = _now()
        for row in rows:
            if row.id == candidate.id:
                out.append(candidate)
                replaced = True
            else:
                out.append(row)
        if not replaced:
            out.append(candidate)
        self._write_all(out)
        return candidate

    # ------------------------------------------------------------------
    # Candidate lifecycle
    # ------------------------------------------------------------------

    def submit(
        self,
        title: str,
        body: str,
        kind: str = "heuristic",
        source_agent_id: str = "unknown",
        source_harness: str = "unknown",
        task_type: Optional[str] = None,
        repo: Optional[str] = None,
        scope: str = "personal",
        confidence: float = 0.5,
        utility: float = 0.0,
        evidence: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "candidate",
        learning_id: Optional[str] = None,
    ) -> LearningCandidate:
        if not str(title or "").strip():
            raise LearningError("learning title is required")
        if not str(body or "").strip():
            raise LearningError("learning body is required")

        evidence_rows = list(evidence or [])
        clean_status = _normalise_status(status)
        rejected_reason = None
        if _has_prompt_injection(f"{title}\n{body}"):
            clean_status = "rejected"
            rejected_reason = "blocked_prompt_injection_pattern"
            evidence_rows.append({"kind": "safety", "reason": rejected_reason})

        candidate = LearningCandidate(
            id=str(learning_id or _new_id()),
            kind=kind,
            title=title,
            body=body,
            source_agent_id=source_agent_id,
            source_harness=source_harness,
            task_type=task_type,
            repo=os.path.abspath(os.path.expanduser(repo)) if repo else None,
            scope=scope,
            confidence=confidence,
            utility=utility,
            evidence=evidence_rows,
            status=clean_status,
            rejected_reason=rejected_reason,
            metadata=dict(metadata or {}),
        )
        return self._upsert(candidate)

    def reject(self, learning_id: str, reason: Optional[str] = None) -> LearningCandidate:
        candidate = self._require(learning_id)
        candidate.status = "rejected"
        candidate.rejected_reason = reason or candidate.rejected_reason or "rejected"
        return self._upsert(candidate)

    def archive(self, learning_id: str) -> LearningCandidate:
        candidate = self._require(learning_id)
        candidate.status = "archived"
        return self._upsert(candidate)

    def record_outcome(
        self,
        learning_id: str,
        success: bool,
        outcome_score: Optional[float] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> LearningCandidate:
        candidate = self._require(learning_id)
        candidate.reuse_count += 1
        if success:
            candidate.success_count += 1
        else:
            candidate.failure_count += 1
        if outcome_score is not None:
            score = _clamp(outcome_score)
            candidate.utility = max(candidate.utility, score)
        total = candidate.success_count + candidate.failure_count
        if total:
            observed = candidate.success_count / float(total)
            if candidate.success_count >= 2 and candidate.failure_count == 0:
                observed = max(observed, 0.7)
            candidate.confidence = max(candidate.confidence, min(1.0, observed))
        if evidence:
            item = dict(evidence)
            item.setdefault("kind", "reuse")
            item.setdefault("success", bool(success))
            candidate.evidence.append(item)
        return self._upsert(candidate)

    def can_auto_promote(self, candidate: LearningCandidate, scope: str = "personal") -> Tuple[bool, str]:
        target_scope = _normalise_scope(scope)
        if candidate.status != "candidate":
            return False, f"status_is_{candidate.status}"
        if target_scope != "personal":
            return False, "repo_or_workspace_requires_explicit_approval"
        if candidate.success_count < 2:
            return False, "needs_at_least_2_successful_reuses"
        if candidate.failure_count:
            return False, "has_unresolved_failure_evidence"
        if candidate.confidence < 0.70:
            return False, "confidence_below_0.70"
        return True, "ok"

    def promote(
        self,
        learning_id: str,
        scope: str = "personal",
        repo: Optional[str] = None,
        approved_by: Optional[str] = None,
    ) -> LearningCandidate:
        candidate = self._require(learning_id)
        target_scope = _normalise_scope(scope)
        if candidate.status == "rejected":
            raise PromotionError("rejected learnings cannot be promoted")
        if candidate.status == "archived":
            raise PromotionError("archived learnings cannot be promoted")
        if target_scope == "personal" and not approved_by:
            ok, reason = self.can_auto_promote(candidate, target_scope)
            if not ok:
                raise PromotionError(reason)
        if target_scope in {"repo", "workspace"} and not approved_by:
            raise PromotionError("repo_or_workspace_requires_explicit_approval")

        candidate.status = "promoted"
        candidate.scope = target_scope
        candidate.promoted_at = _now()
        if repo:
            candidate.repo = os.path.abspath(os.path.expanduser(repo))
        candidate.metadata["approved_by"] = approved_by or "auto_gate"
        promoted = self._upsert(candidate)
        if target_scope == "repo":
            if not promoted.repo:
                raise PromotionError("repo scope requires repo path")
            self.export_repo_learning(promoted.repo, promoted)
        return promoted

    def _require(self, learning_id: str) -> LearningCandidate:
        candidate = self.get(learning_id)
        if not candidate:
            raise LearningError(f"unknown learning: {learning_id}")
        return candidate

    # ------------------------------------------------------------------
    # Retrieval and context
    # ------------------------------------------------------------------

    def search(
        self,
        query: Optional[str] = None,
        task_type: Optional[str] = None,
        repo: Optional[str] = None,
        status: str = "promoted",
        limit: int = 10,
        include_candidates: bool = False,
    ) -> List[Dict[str, Any]]:
        requested_status = _normalise_status(status)
        if include_candidates and requested_status == "promoted":
            statuses = {"promoted", "candidate"}
        else:
            statuses = {requested_status}
        tokens = set(_tokenize(query or ""))
        repo_abs = os.path.abspath(os.path.expanduser(repo)) if repo else None
        scored: List[Tuple[float, LearningCandidate]] = []
        for item in self.list():
            if item.status in {"rejected", "archived"}:
                continue
            if statuses and item.status not in statuses:
                continue
            if task_type and item.task_type and item.task_type != task_type:
                continue
            if repo_abs and item.repo and item.repo != repo_abs:
                continue
            haystack = set(_tokenize(" ".join([item.title, item.body, item.kind, item.task_type or ""])))
            lexical = len(tokens & haystack) / float(len(tokens) or 1)
            score = lexical + item.confidence * 0.25 + item.utility * 0.2 + item.success_count * 0.03
            if not tokens:
                score = item.confidence + item.utility + item.success_count * 0.05
            compact = item.compact()
            compact["score"] = round(score, 3)
            scored.append((score, LearningCandidate.from_dict(compact_to_full(compact, item))))
        scored.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        return [item.compact() | {"score": round(score, 3)} for score, item in scored[: max(1, int(limit or 10))]]

    def context_block(
        self,
        query: Optional[str] = None,
        task_type: Optional[str] = None,
        repo: Optional[str] = None,
        limit: int = 5,
    ) -> str:
        rows = self.search(query=query, task_type=task_type, repo=repo, status="promoted", limit=limit)
        return format_learnings_for_context(rows)

    # ------------------------------------------------------------------
    # Repo export and Hermes import
    # ------------------------------------------------------------------

    def export_repo_learning(self, repo: str, candidate: LearningCandidate) -> Path:
        repo_root = Path(repo).expanduser().resolve()
        context_dir = repo_root / ".dhee" / "context"
        context_dir.mkdir(parents=True, exist_ok=True)
        path = context_dir / "learnings.jsonl"
        rows: List[Dict[str, Any]] = []
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if row.get("id") != candidate.id:
                        rows.append(row)
        row = candidate.compact(max_body_chars=4000)
        row["promoted_at"] = candidate.promoted_at
        rows.append(row)
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for item in rows:
                handle.write(json.dumps(item, sort_keys=True) + "\n")
        os.replace(str(tmp), str(path))
        return path

    def import_hermes_home(
        self,
        hermes_home: Union[os.PathLike, str],
        user_id: str = "default",
        source_agent_id: str = "hermes",
        repo: Optional[str] = None,
        dry_run: bool = False,
        promote: bool = False,
        session_limit: int = 20,
    ) -> Dict[str, Any]:
        root = Path(hermes_home).expanduser()
        candidates: List[LearningCandidate] = []
        skipped: List[Dict[str, str]] = []

        for path, kind, title, allow_instant_promotion in self._hermes_source_files(root):
            text = _safe_read(path)
            if not text:
                continue
            candidates.append(self._candidate_from_import(
                title=title,
                body=text,
                kind=kind,
                source_agent_id=source_agent_id,
                task_type="hermes_import",
                repo=repo,
                source_path=path,
                promote=bool(promote and allow_instant_promotion),
            ))

        for path in self._hermes_agent_skill_files(root):
            text = _safe_read(path)
            if not text:
                continue
            candidates.append(self._candidate_from_import(
                title=f"Hermes skill: {path.parent.name}",
                body=text,
                kind="skill",
                source_agent_id=source_agent_id,
                task_type="hermes_skill",
                repo=repo,
                source_path=path,
                promote=False,
            ))

        for title, body, source_path in self._hermes_session_summaries(root, limit=session_limit):
            candidates.append(self._candidate_from_import(
                title=title,
                body=body,
                kind="workflow",
                source_agent_id=source_agent_id,
                task_type="hermes_session",
                repo=repo,
                source_path=source_path,
                promote=False,
            ))

        existing_by_hash = self._source_hash_map()
        imported: List[LearningCandidate] = []
        updated: List[LearningCandidate] = []
        for candidate in candidates:
            source_hash = _evidence_hash(candidate)
            existing = existing_by_hash.get(source_hash)
            if existing:
                if not dry_run:
                    changed = self._apply_import_policy(existing, candidate)
                    if changed:
                        updated.append(changed)
                skipped.append({"id": candidate.id, "reason": "already_imported", "source_hash": source_hash})
                continue
            if dry_run:
                imported.append(candidate)
            else:
                imported.append(self._upsert(candidate))
                existing_by_hash[source_hash] = candidate

        return {
            "hermes_home": str(root),
            "dry_run": bool(dry_run),
            "promote": bool(promote),
            "imported_count": len(imported),
            "promoted_count": sum(1 for c in imported if c.status == "promoted"),
            "candidate_count": sum(1 for c in imported if c.status == "candidate"),
            "rejected_count": sum(1 for c in imported if c.status == "rejected"),
            "updated_policy_count": len(updated),
            "skipped_count": len(skipped),
            "candidates": [c.compact(max_body_chars=800) for c in imported],
            "updated": [c.compact(max_body_chars=800) for c in updated],
            "skipped": skipped,
        }

    def _candidate_from_import(
        self,
        title: str,
        body: str,
        kind: str,
        source_agent_id: str,
        task_type: str,
        repo: Optional[str],
        source_path: Path,
        promote: bool = False,
    ) -> LearningCandidate:
        source_hash = _text_hash(f"{source_path}\n{body}")
        status = "promoted" if promote else "candidate"
        rejected_reason = None
        evidence = [{
            "kind": "hermes_import",
            "source_path": str(source_path),
            "source_hash": source_hash,
        }]
        if _has_prompt_injection(f"{title}\n{body}"):
            status = "rejected"
            rejected_reason = "blocked_prompt_injection_pattern"
            evidence.append({"kind": "safety", "reason": rejected_reason})
        return LearningCandidate(
            id="lrn_" + source_hash[:16],
            kind=kind,
            title=title,
            body=body,
            source_agent_id=source_agent_id,
            source_harness="hermes",
            task_type=task_type,
            repo=os.path.abspath(os.path.expanduser(repo)) if repo else None,
            confidence=0.5,
            utility=0.4 if promote else 0.0,
            evidence=evidence,
            status=status,
            promoted_at=_now() if promote and status == "promoted" else None,
            rejected_reason=rejected_reason,
            metadata={"approved_by": "hermes_import"} if promote and status == "promoted" else {},
        )

    def _source_hash_map(self) -> Dict[str, LearningCandidate]:
        by_hash: Dict[str, LearningCandidate] = {}
        for candidate in self.list():
            source_hash = _evidence_hash(candidate)
            if source_hash:
                by_hash[source_hash] = candidate
        return by_hash

    def _apply_import_policy(
        self,
        existing: LearningCandidate,
        desired: LearningCandidate,
    ) -> Optional[LearningCandidate]:
        if not _evidence_hash(existing):
            return None
        approved_by = str((existing.metadata or {}).get("approved_by") or "")
        if existing.status == "promoted" and desired.status != "promoted" and approved_by != "hermes_import":
            return None
        if approved_by and approved_by != "hermes_import":
            return None
        if existing.status == desired.status:
            return None

        existing.status = desired.status
        existing.rejected_reason = desired.rejected_reason
        if desired.status == "promoted":
            existing.promoted_at = existing.promoted_at or _now()
            existing.utility = max(existing.utility, desired.utility)
            existing.metadata["approved_by"] = "hermes_import"
        else:
            existing.promoted_at = None
            existing.utility = min(existing.utility, desired.utility)
            existing.metadata.pop("approved_by", None)
        return self._upsert(existing)

    @staticmethod
    def _hermes_source_files(root: Path) -> List[Tuple[Path, str, str, bool]]:
        return [
            (root / "SOUL.md", "workflow", "Hermes SOUL.md", False),
            (root / "MEMORY.md", "memory", "Hermes MEMORY.md", True),
            (root / "USER.md", "memory", "Hermes USER.md", True),
            (root / "memories" / "MEMORY.md", "memory", "Hermes memories/MEMORY.md", True),
            (root / "memories" / "USER.md", "memory", "Hermes memories/USER.md", True),
        ]

    @staticmethod
    def _hermes_agent_skill_files(root: Path) -> List[Path]:
        skills_root = root / "skills"
        if not skills_root.exists():
            return []
        files: List[Path] = []
        ignored_parts = {"hub", "bundled", "builtin", "builtins", "optional-skills", ".cache"}
        for path in skills_root.rglob("*.md"):
            parts = {p.lower() for p in path.parts}
            if parts & ignored_parts:
                continue
            text_head = _safe_read(path, max_chars=1200).lower()
            if "source: hub" in text_head or "hub-installed" in text_head or "bundled skill" in text_head:
                continue
            if path.name.lower() in {"skill.md", "skills.md", "readme.md"} or path.name == "SKILL.md":
                if LearningExchange._matches_bundled_hermes_skill(root, path):
                    continue
                files.append(path)
        return files

    @staticmethod
    def _matches_bundled_hermes_skill(root: Path, path: Path) -> bool:
        try:
            rel = path.relative_to(root / "skills")
        except ValueError:
            return False
        current = _safe_read(path)
        if not current:
            return False
        for source_root in (root / "hermes-agent" / "skills", root / "hermes-agent" / "optional-skills"):
            source = source_root / rel
            if not source.exists():
                continue
            if _safe_read(source) == current:
                return True
        return False

    @staticmethod
    def _hermes_session_summaries(root: Path, limit: int = 20) -> List[Tuple[str, str, Path]]:
        state_db = root / "state.db"
        if state_db.exists():
            rows = _session_summaries_from_state_db(state_db, limit=limit)
            if rows:
                return rows
        return _session_summaries_from_json_files(root / "sessions", limit=limit)


def compact_to_full(compact: Dict[str, Any], source: LearningCandidate) -> Dict[str, Any]:
    data = source.to_dict()
    data.update({k: v for k, v in compact.items() if k in data})
    return data


def _safe_read(path: Path, max_chars: Optional[int] = None) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if max_chars is not None:
        return text[:max_chars]
    return text.strip()


def _evidence_hash(candidate: LearningCandidate) -> str:
    for item in candidate.evidence:
        if isinstance(item, dict) and item.get("source_hash"):
            return str(item["source_hash"])
    return ""


def _session_summaries_from_state_db(path: Path, limit: int = 20) -> List[Tuple[str, str, Path]]:
    try:
        con = sqlite3.connect(str(path))
        con.row_factory = sqlite3.Row
        sessions = con.execute(
            "select id, title, model, source, started_at, ended_at, message_count "
            "from sessions order by started_at desc limit ?",
            (max(1, min(100, int(limit or 20))),),
        ).fetchall()
    except Exception:
        return []

    results: List[Tuple[str, str, Path]] = []
    try:
        for session in sessions:
            messages = con.execute(
                "select role, content from messages where session_id = ? "
                "and content is not null order by timestamp asc",
                (session["id"],),
            ).fetchall()
            body = _format_session_summary(dict(session), [dict(m) for m in messages])
            if not body:
                continue
            title = _clean_session_title(session["title"], session["id"], [dict(m) for m in messages])
            results.append((title, body, Path(f"{path}#{session['id']}")))
    finally:
        try:
            con.close()
        except Exception:
            pass
    return results


def _session_summaries_from_json_files(path: Path, limit: int = 20) -> List[Tuple[str, str, Path]]:
    if not path.exists():
        return []
    files = sorted(path.glob("session_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows: List[Tuple[str, str, Path]] = []
    for file_path in files[: max(1, min(100, int(limit or 20)))]:
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        messages = data.get("messages") or data.get("conversation") or []
        if not isinstance(messages, list):
            messages = []
        session = {
            "id": data.get("id") or file_path.stem,
            "title": data.get("title") or file_path.stem,
            "model": data.get("model"),
            "source": data.get("source") or "json_session",
            "started_at": data.get("started_at"),
            "ended_at": data.get("ended_at"),
            "message_count": len(messages),
        }
        body = _format_session_summary(session, messages)
        if body:
            rows.append((_clean_session_title(session["title"], session["id"], messages), body, file_path))
    return rows


def _format_session_summary(session: Dict[str, Any], messages: List[Dict[str, Any]]) -> str:
    head = [
        f"Session: {session.get('id')}",
        f"Source: {session.get('source') or 'hermes'}",
        f"Model: {session.get('model') or 'unknown'}",
        f"Messages: {session.get('message_count') or len(messages)}",
    ]
    selected: List[Dict[str, Any]] = []
    if messages:
        selected.extend(messages[:3])
        if len(messages) > 6:
            selected.append({"role": "system", "content": "... middle turns omitted ..."})
        selected.extend(messages[-3:])
    seen = set()
    lines: List[str] = []
    for message in selected:
        role = str(message.get("role") or message.get("speaker") or "message")
        content = str(message.get("content") or message.get("text") or "").strip()
        if not content:
            continue
        if role == "system" and content != "... middle turns omitted ...":
            continue
        content = " ".join(content.split())
        if len(content) > 800:
            content = content[:799] + "..."
        key = (role, content)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{role}: {content}")
    if not lines:
        return ""
    return "\n".join(head + ["", "Representative turns:"] + lines)


def _clean_session_title(raw_title: Any, session_id: Any, messages: List[Dict[str, Any]]) -> str:
    title = " ".join(str(raw_title or "").strip().split())
    if title.startswith("<think>") or len(title) > 90:
        title = ""
    if not title:
        for message in messages:
            role = str(message.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            content = " ".join(str(message.get("content") or "").split())
            if content:
                title = content[:76].rstrip()
                break
    if not title:
        title = f"Hermes session {session_id}"
    if len(title) > 80:
        title = title[:79].rstrip() + "..."
    return title


def format_learnings_for_context(rows: Iterable[Dict[str, Any]], max_items: int = 5) -> str:
    selected = list(rows)[:max_items]
    if not selected:
        return ""
    parts = ["### Learned Playbooks"]
    for item in selected:
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        confidence = item.get("confidence", 0)
        scope = item.get("scope", "personal")
        if len(body) > 350:
            body = body[:349].rstrip() + "..."
        parts.append(f"- {title} [{scope}, confidence={float(confidence):.0%}]: {body}")
    return "\n".join(parts)
