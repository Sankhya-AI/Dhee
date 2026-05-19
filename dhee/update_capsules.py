"""Repo-shareable update capsules.

An update capsule is a sanitized recipe, not an auto-applied patch.  It gives
another agent the before/after story, changed interfaces, compact hunks,
hashes, commands, and evidence pointers needed to recreate behavior with
normal editing and verification tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dhee.context_ir import build_context_ir, interpret_context_ir, render_context_ir, validate_context_ir
from dhee import repo_link


CAPSULE_SCHEMA_VERSION = 1
CAPSULE_KIND = "update_capsule"
MAX_DIFF_CHARS_PER_FILE = 18_000
MAX_MD_DIFF_CHARS = 4_000

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]
_LOCAL_PATH_RE = re.compile(r"(/Users/[^\s\"']+|/home/[^\s\"']+|[A-Za-z]:\\\\[^\s\"']+)")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, default=str)


def _stable_hash(data: Any, length: int = 18) -> str:
    raw = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _sanitize_text(text: str) -> str:
    value = str(text or "")
    home = str(Path.home())
    if home:
        value = value.replace(home, "$HOME")
    value = _LOCAL_PATH_RE.sub("<local-path>", value)
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("<redacted-secret>", value)
    return value


def _sanitize_obj(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_obj(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_obj(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_obj(item) for key, item in value.items()}
    return value


def _run_git(repo_root: Path, args: List[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise ValueError((proc.stderr or proc.stdout or "git command failed").strip())
    return proc


def _git_out(repo_root: Path, args: List[str], default: str = "") -> str:
    proc = _run_git(repo_root, args)
    if proc.returncode != 0:
        return default
    return proc.stdout.strip()


def _resolve_repo_root(repo: str | os.PathLike[str] | None) -> Path:
    base = Path(repo or os.getcwd()).expanduser().resolve()
    proc = subprocess.run(
        ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    return base


def _is_valid_ref(repo_root: Path, ref: str) -> bool:
    if not ref:
        return False
    return _run_git(repo_root, ["rev-parse", "--verify", f"{ref}^{{commit}}"]).returncode == 0


def _is_dhee_generated_context_path(rel_path: str) -> bool:
    path = str(rel_path or "").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path == ".dhee" or path.startswith(".dhee/")


def _changed_paths(repo_root: Path, since: Optional[str]) -> List[Dict[str, Any]]:
    by_path: Dict[str, Dict[str, Any]] = {}
    if since and _is_valid_ref(repo_root, since):
        for path in _git_out(repo_root, ["diff", "--name-only", since, "--"]).splitlines():
            if path.strip():
                by_path[path.strip()] = {"path": path.strip(), "status": "modified"}
    else:
        for path in _git_out(repo_root, ["diff", "--name-only", "--"]).splitlines():
            if path.strip():
                by_path[path.strip()] = {"path": path.strip(), "status": "modified"}

    status = _git_out(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"])
    for line in status.splitlines():
        if not line:
            continue
        code = line[:2]
        raw_path = (line[3:] if len(line) > 2 and line[2] == " " else line[2:]).strip()
        if " -> " in raw_path:
            _old, raw_path = raw_path.split(" -> ", 1)
        status_name = _status_name(code)
        by_path.setdefault(raw_path, {"path": raw_path, "status": status_name})
        by_path[raw_path]["status"] = status_name
    return [
        by_path[key]
        for key in sorted(by_path)
        if not _is_dhee_generated_context_path(key)
    ]


def _status_name(code: str) -> str:
    code = code or ""
    if "?" in code:
        return "untracked"
    if "D" in code:
        return "deleted"
    if "A" in code:
        return "added"
    if "R" in code:
        return "renamed"
    if "M" in code:
        return "modified"
    return "changed"


def _file_hash(repo_root: Path, rel_path: str) -> Optional[str]:
    path = (repo_root / rel_path).resolve()
    try:
        if not path.exists() or not path.is_file():
            return None
        if not str(path).startswith(str(repo_root.resolve())):
            return None
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _file_size(repo_root: Path, rel_path: str) -> Optional[int]:
    try:
        path = repo_root / rel_path
        if path.exists() and path.is_file():
            return path.stat().st_size
    except OSError:
        return None
    return None


def _git_blob_hash(repo_root: Path, ref: str, rel_path: str) -> Optional[str]:
    if not ref or not rel_path or not _is_valid_ref(repo_root, ref):
        return None
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{ref}:{rel_path}"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return hashlib.sha256(proc.stdout).hexdigest()


def _diff_for_path(repo_root: Path, rel_path: str, since: Optional[str]) -> Tuple[str, bool]:
    args = ["diff", "--no-ext-diff", "--unified=3"]
    if since and _is_valid_ref(repo_root, since):
        args.append(since)
    args.extend(["--", rel_path])
    diff = _git_out(repo_root, args)
    truncated = len(diff) > MAX_DIFF_CHARS_PER_FILE
    if truncated:
        diff = diff[:MAX_DIFF_CHARS_PER_FILE].rstrip() + "\n[diff truncated]"
    return _sanitize_text(diff), truncated


def _status_summary(repo_root: Path) -> Dict[str, Any]:
    status = _git_out(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"])
    lines = [line for line in status.splitlines() if line.strip()]
    return {
        "dirty": bool(lines),
        "porcelain": [_sanitize_text(line) for line in lines[:200]],
        "untracked_count": sum(1 for line in lines if line.startswith("??")),
        "changed_count": len(lines),
    }


def _compact_evidence_pointers(evidence: Optional[Iterable[Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    pointers: List[Dict[str, Any]] = []
    personal_used = False
    for item in evidence or []:
        if not isinstance(item, dict):
            item = {"ref": str(item), "kind": "evidence"}
        scope = str(item.get("confidentiality_scope") or item.get("privacy_scope") or "personal")
        if scope == "personal":
            personal_used = True
        if scope in {"secret", "restricted"}:
            personal_used = True
            continue
        safe = {
            "kind": str(item.get("kind") or item.get("memory_type") or "evidence"),
            "ref": str(item.get("ref") or item.get("id") or item.get("memory_id") or _stable_hash(item, 12)),
            "label": _sanitize_text(str(item.get("label") or item.get("title") or ""))[:160],
            "source_app": str(item.get("source_app") or ""),
            "agent_id": str(item.get("agent_id") or ""),
            "source_event_id": str(item.get("source_event_id") or ""),
            "run_id": str(item.get("run_id") or ""),
            "modality": str(item.get("modality") or "text"),
            "confidentiality_scope": "redacted" if scope == "personal" else scope,
        }
        pointers.append(safe)
    return pointers, personal_used


@dataclass
class UpdateCapsule:
    id: str
    title: str
    summary: str
    repo_root: str
    repo_id: str
    base_ref: str
    base_commit: str
    head_commit: str
    created_at: str
    changed_paths: List[Dict[str, Any]] = field(default_factory=list)
    base_file_hashes: Dict[str, str] = field(default_factory=dict)
    file_hashes: Dict[str, str] = field(default_factory=dict)
    compact_hunks: List[Dict[str, Any]] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    evidence_pointers: List[Dict[str, Any]] = field(default_factory=list)
    compatibility_notes: List[str] = field(default_factory=list)
    personal_context_used: bool = False
    privacy: Dict[str, Any] = field(default_factory=dict)
    context_ir: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": CAPSULE_SCHEMA_VERSION,
            "kind": CAPSULE_KIND,
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "repo": {
                "root_name": Path(self.repo_root).name,
                "repo_id": self.repo_id,
                "base_ref": self.base_ref,
                "base_commit": self.base_commit,
                "head_commit": self.head_commit,
            },
            "base_ref": self.base_ref,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "created_at": self.created_at,
            "changed_paths": self.changed_paths,
            "base_file_hashes": self.base_file_hashes,
            "file_hashes": self.file_hashes,
            "compact_hunks": self.compact_hunks,
            "commands": self.commands,
            "evidence_pointers": self.evidence_pointers,
            "compatibility_notes": self.compatibility_notes,
            "personal_context_used": self.personal_context_used,
            "privacy": self.privacy,
            "context_ir": self.context_ir,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "UpdateCapsule":
        repo = raw.get("repo") or {}
        return cls(
            id=str(raw.get("id") or _stable_hash(raw)),
            title=str(raw.get("title") or ""),
            summary=str(raw.get("summary") or ""),
            repo_root=str(raw.get("repo_root") or repo.get("root_name") or ""),
            repo_id=str(raw.get("repo_id") or repo.get("repo_id") or ""),
            base_ref=str(raw.get("base_ref") or repo.get("base_ref") or ""),
            base_commit=str(raw.get("base_commit") or repo.get("base_commit") or ""),
            head_commit=str(raw.get("head_commit") or repo.get("head_commit") or ""),
            created_at=str(raw.get("created_at") or _now_iso()),
            changed_paths=list(raw.get("changed_paths") or []),
            base_file_hashes=dict(raw.get("base_file_hashes") or {}),
            file_hashes=dict(raw.get("file_hashes") or {}),
            compact_hunks=list(raw.get("compact_hunks") or []),
            commands=list(raw.get("commands") or []),
            evidence_pointers=list(raw.get("evidence_pointers") or []),
            compatibility_notes=list(raw.get("compatibility_notes") or []),
            personal_context_used=bool(raw.get("personal_context_used") or False),
            privacy=dict(raw.get("privacy") or {}),
            context_ir=dict(raw.get("context_ir") or {}),
            metadata=dict(raw.get("metadata") or {}),
        )


def render_capsule_markdown(capsule: UpdateCapsule) -> str:
    changed = capsule.changed_paths
    paths = "\n".join(
        f"- `{item.get('path')}` ({item.get('status') or 'changed'})"
        for item in changed
    ) or "- No changed paths detected."
    hunks: List[str] = []
    for hunk in capsule.compact_hunks:
        diff = str(hunk.get("diff") or "")
        if len(diff) > MAX_MD_DIFF_CHARS:
            diff = diff[:MAX_MD_DIFF_CHARS].rstrip() + "\n[diff clipped in markdown; see capsule.json]"
        if diff:
            hunks.append(f"### {hunk.get('path')}\n\n```diff\n{diff}\n```")
        else:
            hunks.append(f"### {hunk.get('path')}\n\nNo compact diff available, usually because the file is untracked or binary.")
    hunk_text = "\n\n".join(hunks) or "No compact hunks captured."
    commands = "\n".join(f"- `{cmd}`" for cmd in capsule.commands) or "- No test command was recorded."
    evidence = "\n".join(
        f"- `{ptr.get('kind')}` `{ptr.get('ref')}`"
        + (f" from {ptr.get('source_app')}" if ptr.get("source_app") else "")
        + (f" via {ptr.get('agent_id')}" if ptr.get("agent_id") else "")
        for ptr in capsule.evidence_pointers
    ) or "- No shareable evidence pointers were attached."
    compatibility = "\n".join(f"- {note}" for note in capsule.compatibility_notes) or "- No compatibility notes."
    ir_summary = render_context_ir(capsule.context_ir) if capsule.context_ir else "- No Context IR compiled."
    md = f"""# {capsule.title}

## Intent
{capsule.summary}

## Before
Base ref: `{capsule.base_ref or '(unspecified)'}`
Base commit: `{capsule.base_commit or '(unknown)'}`

## After
Head commit at capture: `{capsule.head_commit or '(unknown)'}`
Changed paths captured: {len(changed)}

## Touched Interfaces
{paths}

## Compact Hunks
{hunk_text}

## Reproduction Guide
1. Read `capsule.json` for exact paths, hashes, and compact hunks.
2. Recreate the behavior with normal editing tools; this capsule is context, not an auto-apply patch.
3. Run the recorded commands or the nearest repo test suite.
4. Compare final file hashes or behavior against the capsule notes when useful.

## Context IR
{ir_summary}
- Interpreter policy: validate schema, resolve file symbols on the target repo, produce an execution plan, never auto-apply.

## Tests And Commands
{commands}

## Evidence Pointers
{evidence}

## Privacy And Sharing
- Raw personal memories, screenshots, transcripts, media, local paths, and secrets are not included.
- `personal_context_used`: `{str(capsule.personal_context_used).lower()}`
- Share scope: `{capsule.privacy.get('share_scope') or 'repo'}`

## Compatibility Notes
{compatibility}
"""
    return _sanitize_text(md).strip() + "\n"


def _capsule_root(repo_root: Path) -> Path:
    return repo_link.repo_context_dir(repo_root) / "capsules"


def _write_capsule(capsule: UpdateCapsule, capsule_dir: Path) -> Dict[str, str]:
    capsule_dir.mkdir(parents=True, exist_ok=True)
    data = _sanitize_obj(capsule.to_dict())
    md = _sanitize_text(render_capsule_markdown(capsule))
    json_path = capsule_dir / "capsule.json"
    md_path = capsule_dir / "capsule.md"
    json_path.write_text(_json_dumps(data) + "\n", encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path), "dir": str(capsule_dir)}


def create_update_capsule(
    *,
    repo: str | os.PathLike[str] | None = None,
    since: Optional[str] = None,
    task_id: Optional[str] = None,
    out: Optional[str | os.PathLike[str]] = None,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    commands: Optional[List[str]] = None,
    evidence: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    repo_root = _resolve_repo_root(repo)
    repo_id = repo_link._ensure_repo_skeleton(repo_root)
    base_ref = since or "HEAD"
    base_commit = _git_out(repo_root, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"], default="")
    if not base_commit and since:
        raise ValueError(f"Base ref {since!r} is not a valid commit")
    head_commit = _git_out(repo_root, ["rev-parse", "--verify", "HEAD^{commit}"], default="")
    status = _status_summary(repo_root)
    changed_paths = _changed_paths(repo_root, since)
    base_file_hashes: Dict[str, str] = {}
    file_hashes: Dict[str, str] = {}
    compact_hunks: List[Dict[str, Any]] = []
    for item in changed_paths:
        rel_path = str(item.get("path") or "")
        base_digest = _git_blob_hash(repo_root, base_ref, rel_path)
        if base_digest:
            base_file_hashes[rel_path] = base_digest
            item["base_sha256"] = base_digest
        digest = _file_hash(repo_root, rel_path)
        if digest:
            file_hashes[rel_path] = digest
            item["sha256"] = digest
        size = _file_size(repo_root, rel_path)
        if size is not None:
            item["size"] = size
        diff, truncated = _diff_for_path(repo_root, rel_path, since)
        compact_hunks.append({
            "path": rel_path,
            "status": item.get("status") or "changed",
            "diff": diff,
            "truncated": truncated,
        })
    evidence_pointers, evidence_personal = _compact_evidence_pointers(evidence)
    personal_context_used = bool(evidence_personal)
    capsule_title = title or f"Update capsule {task_id or (base_ref + ' -> worktree')}"
    capsule_summary = summary or (
        f"Captured {len(changed_paths)} changed path(s) from {base_ref} to the current worktree."
    )
    capsule_commands = _sanitize_obj(commands or [
        f"git diff --stat {base_ref}",
        "git status --short",
    ])
    capsule_privacy = {
        "share_scope": "repo",
        "raw_personal_memory_included": False,
        "raw_media_included": False,
        "screenshots_included": False,
        "transcripts_included": False,
        "local_paths_redacted": True,
        "secrets_redacted": True,
        "redaction_applied": True,
        "promotion_required_for_personal_lessons": True,
    }
    compatibility_notes = [
        "Capsule is a hybrid recipe; V1 does not auto-apply patches.",
        "Whole-file snapshots are not stored by default.",
        "Context IR is interpreted on the receiving machine before any edits are attempted.",
    ]
    if status["dirty"]:
        compatibility_notes.append("Worktree was dirty at capture time; verify staged and unstaged edits explicitly.")
    if any(item.get("status") == "untracked" for item in changed_paths):
        compatibility_notes.append("Untracked files are listed with hashes but no git diff body.")
    payload_for_id = {
        "repo_id": repo_id,
        "base_commit": base_commit,
        "head_commit": head_commit,
        "changed_paths": changed_paths,
        "compact_hunks_hash": _stable_hash(compact_hunks, 24),
        "task_id": task_id or "",
    }
    capsule_id = "ucap_" + _stable_hash(payload_for_id, 20)
    context_ir = build_context_ir(
        capsule_id=capsule_id,
        title=_sanitize_text(capsule_title),
        summary=_sanitize_text(capsule_summary),
        repo_id=repo_id,
        base_ref=base_ref,
        base_commit=base_commit,
        head_commit=head_commit,
        changed_paths=_sanitize_obj(changed_paths),
        compact_hunks=_sanitize_obj(compact_hunks),
        commands=capsule_commands,
        evidence_pointers=evidence_pointers,
        base_file_hashes=_sanitize_obj(base_file_hashes),
        file_hashes=_sanitize_obj(file_hashes),
        privacy=capsule_privacy,
    )
    capsule = UpdateCapsule(
        id=capsule_id,
        title=_sanitize_text(capsule_title),
        summary=_sanitize_text(capsule_summary),
        repo_root=str(repo_root),
        repo_id=repo_id,
        base_ref=base_ref,
        base_commit=base_commit,
        head_commit=head_commit,
        created_at=_now_iso(),
        changed_paths=_sanitize_obj(changed_paths),
        base_file_hashes=_sanitize_obj(base_file_hashes),
        file_hashes=_sanitize_obj(file_hashes),
        compact_hunks=_sanitize_obj(compact_hunks),
        commands=capsule_commands,
        evidence_pointers=evidence_pointers,
        compatibility_notes=compatibility_notes,
        personal_context_used=personal_context_used,
        privacy=capsule_privacy,
        context_ir=context_ir,
        metadata={
            "task_id": task_id or "",
            "status": status,
            "capsule_payload": "hybrid_recipe_not_patch_only",
            "compiler": "dhee-context-compiler",
        },
    )
    capsule_dir = Path(out).expanduser().resolve() if out else _capsule_root(repo_root) / capsule.id
    paths = _write_capsule(capsule, capsule_dir)
    md = Path(paths["markdown"]).read_text(encoding="utf-8")
    rel_dir = os.path.relpath(capsule_dir, repo_root) if str(capsule_dir).startswith(str(repo_root)) else str(capsule_dir)
    entry = repo_link.add_entry(
        repo_root,
        kind=CAPSULE_KIND,
        title=capsule.title,
        content=md,
        meta={
            "capsule_id": capsule.id,
            "capsule_dir": rel_dir,
            "base_commit": capsule.base_commit,
            "head_commit": capsule.head_commit,
            "changed_paths": [item.get("path") for item in capsule.changed_paths],
            "personal_context_used": capsule.personal_context_used,
            "privacy": capsule.privacy,
        },
    )
    return {
        "format": "dhee_update_capsule_create.v1",
        "capsule": capsule.to_dict(),
        "paths": paths,
        "entry": entry.to_json(),
    }


def list_update_capsules(*, repo: str | os.PathLike[str] | None = None) -> List[Dict[str, Any]]:
    repo_root = _resolve_repo_root(repo)
    root = _capsule_root(repo_root)
    if not root.exists():
        return []
    capsules: List[Dict[str, Any]] = []
    for json_path in sorted(root.glob("*/capsule.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        capsules.append({
            "id": data.get("id"),
            "title": data.get("title"),
            "created_at": data.get("created_at"),
            "changed_paths": data.get("changed_paths") or [],
            "path": str(json_path.parent),
            "personal_context_used": bool(data.get("personal_context_used")),
        })
    return capsules


def get_update_capsule(
    capsule_id: str,
    *,
    repo: str | os.PathLike[str] | None = None,
) -> Dict[str, Any]:
    repo_root = _resolve_repo_root(repo)
    root = _capsule_root(repo_root)
    matches = [path for path in root.glob("*/capsule.json") if path.parent.name == capsule_id or path.parent.name.startswith(capsule_id)]
    if not matches:
        raise FileNotFoundError(f"Update capsule {capsule_id!r} not found")
    json_path = matches[0]
    md_path = json_path.with_name("capsule.md")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return {
        "format": "dhee_update_capsule_get.v1",
        "capsule": data,
        "markdown": md_path.read_text(encoding="utf-8") if md_path.exists() else "",
        "paths": {"json": str(json_path), "markdown": str(md_path), "dir": str(json_path.parent)},
    }


def _load_capsule_data(
    capsule: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
) -> Dict[str, Any]:
    if isinstance(capsule, dict):
        return capsule
    value = str(capsule)
    source = Path(value).expanduser()
    if source.exists():
        data, _md, _source_dir = _read_import_source(source)
        return data
    return get_update_capsule(value, repo=repo)["capsule"]


def interpret_update_capsule(
    capsule: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Interpret a compiled capsule on the target repo without applying it."""

    data = _load_capsule_data(capsule, repo=repo)
    return interpret_context_ir(repo=repo, capsule_or_ir=data, strict=strict)


def _read_import_source(path: str | os.PathLike[str]) -> Tuple[Dict[str, Any], str, Path]:
    source = Path(path).expanduser().resolve()
    if source.is_dir():
        json_path = source / "capsule.json"
        md_path = source / "capsule.md"
        source_dir = source
    elif source.suffix == ".json":
        json_path = source
        md_path = source.with_name("capsule.md")
        source_dir = source.parent
    elif source.suffix == ".md":
        json_path = source.with_name("capsule.json")
        md_path = source
        source_dir = source.parent
    else:
        raise ValueError("Import path must be a capsule directory, capsule.json, or capsule.md")
    if not json_path.exists():
        raise FileNotFoundError(f"Missing capsule.json near {source}")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else render_capsule_markdown(UpdateCapsule.from_dict(data))
    return data, md, source_dir


def import_update_capsule(
    path: str | os.PathLike[str],
    *,
    repo: str | os.PathLike[str] | None = None,
    allow_private: bool = False,
) -> Dict[str, Any]:
    repo_root = _resolve_repo_root(repo)
    repo_link._ensure_repo_skeleton(repo_root)
    data, md, _source_dir = _read_import_source(path)
    privacy = data.get("privacy") or {}
    if privacy.get("raw_personal_memory_included") and not allow_private:
        raise ValueError("Capsule import rejected: raw personal memory is marked as included")
    data = _sanitize_obj(data)
    if isinstance(data.get("context_ir"), dict) and data["context_ir"]:
        validation = validate_context_ir(data["context_ir"], strict=True)
        if not validation["ok"]:
            codes = ", ".join(
                str(item.get("code"))
                for item in validation["diagnostics"]
                if item.get("level") == "error"
            )
            raise ValueError(f"Capsule import rejected: invalid context_ir ({codes or 'validation failed'})")
    md = _sanitize_text(md)
    capsule_id = str(data.get("id") or ("ucap_" + _stable_hash(data, 20)))
    data["id"] = capsule_id
    data["kind"] = CAPSULE_KIND
    dest = _capsule_root(repo_root) / capsule_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "capsule.json").write_text(_json_dumps(data) + "\n", encoding="utf-8")
    (dest / "capsule.md").write_text(md, encoding="utf-8")
    entry = repo_link.add_entry(
        repo_root,
        kind=CAPSULE_KIND,
        title=str(data.get("title") or capsule_id),
        content=md,
        meta={
            "capsule_id": capsule_id,
            "capsule_dir": os.path.relpath(dest, repo_root),
            "imported": True,
            "personal_context_used": bool(data.get("personal_context_used")),
            "privacy": data.get("privacy") or {},
        },
    )
    return {
        "format": "dhee_update_capsule_import.v1",
        "capsule": data,
        "paths": {"dir": str(dest), "json": str(dest / "capsule.json"), "markdown": str(dest / "capsule.md")},
        "entry": entry.to_json(),
    }
