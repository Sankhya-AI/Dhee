"""Release hygiene checks for premium Dhee builds.

This module is intentionally deterministic.  It does not decide whether a
change is good; it proves whether the repo is in a releasable state and records
the intended scope when it is not.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from dhee.runtime_io import read_json_checked, write_json_atomic


RELEASE_INTENT_SCHEMA = "dhee.release_intent.v1"
RELEASE_CHECK_SCHEMA = "dhee.release_check.v1"
RELEASE_INTENT_REL_PATH = ".dhee/context/release_intent.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_git(
    repo: Path,
    args: Sequence[str],
    *,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=text,
        check=False,
    )


def _repo_root(repo: str | os.PathLike[str] | None) -> Dict[str, Any]:
    start = Path(repo or os.getcwd()).expanduser()
    proc = _run_git(start, ["rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        return {
            "ok": False,
            "repo": str(start),
            "diagnostic": {
                "code": "GIT_REPO_UNAVAILABLE",
                "message": (proc.stderr or proc.stdout or "Not inside a git repository.").strip(),
                "path": str(start),
            },
        }
    return {"ok": True, "repo": proc.stdout.strip()}


def _git_text(repo_root: Path, args: Sequence[str]) -> str:
    proc = _run_git(repo_root, args)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _decode_git_path(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


def _parse_porcelain_z(raw: bytes) -> List[Dict[str, Any]]:
    """Parse `git status --porcelain=v1 -z` output.

    Git's NUL format is the only reliable way to handle spaces and unusual
    filenames.  Rename/copy records contain an extra source path; both paths are
    kept so scope checks cannot miss a moved file.
    """

    entries: List[Dict[str, Any]] = []
    parts = raw.split(b"\0")
    idx = 0
    while idx < len(parts):
        record = parts[idx]
        idx += 1
        if not record:
            continue
        status = _decode_git_path(record[:2])
        path = _decode_git_path(record[3:]) if len(record) > 3 else ""
        paths = [path] if path else []
        if ("R" in status or "C" in status) and idx < len(parts) and parts[idx]:
            paths.append(_decode_git_path(parts[idx]))
            idx += 1
        entries.append(
            {
                "status": status,
                "path": path,
                "paths": paths,
                "kind": "untracked" if status == "??" else "ignored" if status == "!!" else "tracked_change",
                "staged": bool(status[:1].strip() and status[:1] not in {"?", "!"}),
                "unstaged": bool(status[1:2].strip() and status[1:2] not in {"?", "!"}),
            }
        )
    return entries


def _normalize_repo_path(repo_root: Path, value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        try:
            rel = path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
            text = rel.as_posix()
        except ValueError:
            return path.as_posix()
    else:
        text = Path(raw).as_posix()
    while text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    return text or "."


def _normalize_paths(repo_root: Path, paths: Optional[Iterable[str]]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for item in paths or []:
        text = _normalize_repo_path(repo_root, str(item))
        if text and text not in seen:
            normalized.append(text)
            seen.add(text)
    return normalized


def _matches_intent(path: str, intended_paths: Sequence[str]) -> bool:
    clean = path.rstrip("/")
    for intended in intended_paths:
        prefix = intended.rstrip("/")
        if prefix == ".":
            return True
        if clean == prefix or clean.startswith(prefix + "/"):
            return True
    return False


def _blocker(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"code": code, "message": message, **extra}


def _unique_paths(entries: Sequence[Dict[str, Any]]) -> List[str]:
    seen = set()
    paths: List[str] = []
    for entry in entries:
        for path in entry.get("paths") or []:
            if path and path not in seen:
                paths.append(path)
                seen.add(path)
    return paths


def release_intent_path(repo: str | os.PathLike[str] | None = None) -> Path:
    root = _repo_root(repo)
    if not root.get("ok"):
        return Path(repo or os.getcwd()).expanduser() / RELEASE_INTENT_REL_PATH
    return Path(root["repo"]) / RELEASE_INTENT_REL_PATH


def load_release_intent(repo: str | os.PathLike[str] | None = None) -> Dict[str, Any]:
    root = _repo_root(repo)
    if not root.get("ok"):
        return {
            "ok": False,
            "exists": False,
            "path": str(Path(repo or os.getcwd()).expanduser() / RELEASE_INTENT_REL_PATH),
            "intended_paths": [],
            "diagnostics": [root["diagnostic"]],
        }

    repo_root = Path(root["repo"])
    path = repo_root / RELEASE_INTENT_REL_PATH
    checked = read_json_checked(path, expected_schema=RELEASE_INTENT_SCHEMA)
    diagnostics = checked.get("diagnostics") or []
    data = checked.get("data") or {}
    intended_paths = _normalize_paths(repo_root, data.get("intended_paths") or [])
    return {
        "ok": bool(checked.get("ok")) or not bool(checked.get("exists")),
        "exists": bool(checked.get("exists")),
        "path": str(path),
        "schema_version": data.get("schema_version"),
        "reason": data.get("reason") or "",
        "created_by": data.get("created_by") or "",
        "generated_at": data.get("generated_at") or "",
        "intended_paths": intended_paths,
        "diagnostics": diagnostics if checked.get("exists") else [],
    }


def write_release_intent(
    repo: str | os.PathLike[str] | None,
    paths: Sequence[str],
    *,
    reason: str = "",
    agent_id: str = "cli",
) -> Dict[str, Any]:
    root = _repo_root(repo)
    if not root.get("ok"):
        return {"ok": False, "diagnostics": [root["diagnostic"]]}
    repo_root = Path(root["repo"])
    intended_paths = _normalize_paths(repo_root, paths)
    payload = {
        "schema_version": RELEASE_INTENT_SCHEMA,
        "generated_at": _now_iso(),
        "created_by": agent_id,
        "reason": reason or "",
        "intended_paths": intended_paths,
    }
    path = repo_root / RELEASE_INTENT_REL_PATH
    write_result = write_json_atomic(path, payload)
    return {
        "ok": bool(write_result.get("ok")),
        "repo": str(repo_root),
        "path": str(path),
        "intent": payload,
        "diagnostics": [] if write_result.get("ok") else [write_result.get("diagnostic")],
    }


def git_status_entries(repo: str | os.PathLike[str] | None = None) -> Dict[str, Any]:
    root = _repo_root(repo)
    if not root.get("ok"):
        return {"ok": False, "repo": root.get("repo"), "entries": [], "diagnostics": [root["diagnostic"]]}
    repo_root = Path(root["repo"])
    proc = _run_git(repo_root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"], text=False)
    if proc.returncode != 0:
        return {
            "ok": False,
            "repo": str(repo_root),
            "entries": [],
            "diagnostics": [
                {
                    "code": "GIT_STATUS_FAILED",
                    "message": _decode_git_path(proc.stderr or proc.stdout or b"git status failed").strip(),
                    "path": str(repo_root),
                }
            ],
        }
    return {"ok": True, "repo": str(repo_root), "entries": _parse_porcelain_z(proc.stdout or b""), "diagnostics": []}


def release_check(
    repo: str | os.PathLike[str] | None = None,
    *,
    intended_paths: Optional[Sequence[str]] = None,
    require_clean: bool = True,
    expected_artifacts: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo)
    if not root.get("ok"):
        return {
            "schema_version": RELEASE_CHECK_SCHEMA,
            "generated_at": _now_iso(),
            "repo": root.get("repo"),
            "status": "blocked",
            "release_allowed": False,
            "release_blockers": [root["diagnostic"]],
            "warnings": [],
        }

    repo_root = Path(root["repo"])
    intent = load_release_intent(repo_root)
    cli_intended = _normalize_paths(repo_root, intended_paths)
    combined_intended = _normalize_paths(repo_root, [*(intent.get("intended_paths") or []), *cli_intended])
    status = git_status_entries(repo_root)
    entries = status.get("entries") or []
    dirty_paths = _unique_paths(entries)
    unexpected_dirty_paths = [
        path for path in dirty_paths if not _matches_intent(path, combined_intended)
    ]

    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if intent.get("exists") and not intent.get("ok"):
        blockers.append(
            _blocker(
                "RELEASE_INTENT_UNREADABLE",
                "Release intent exists but cannot be trusted.",
                diagnostics=intent.get("diagnostics") or [],
            )
        )
    if not status.get("ok"):
        blockers.extend(status.get("diagnostics") or [])
    if dirty_paths and require_clean:
        blockers.append(
            _blocker(
                "GIT_WORKTREE_DIRTY",
                "Release tag is blocked until git status is clean.",
                paths=dirty_paths,
            )
        )
    if unexpected_dirty_paths:
        blockers.append(
            _blocker(
                "UNEXPECTED_DIRTY_PATHS",
                "Dirty paths are outside the documented release intent.",
                paths=unexpected_dirty_paths,
            )
        )
    if dirty_paths and not combined_intended:
        warnings.append(
            _blocker(
                "RELEASE_INTENT_MISSING",
                "Dirty work has no release intent file or --intended-path scope.",
            )
        )

    missing_artifacts: List[str] = []
    for artifact in expected_artifacts or []:
        rel = _normalize_repo_path(repo_root, artifact)
        if not (repo_root / rel).exists():
            missing_artifacts.append(rel)
    if missing_artifacts:
        blockers.append(
            _blocker(
                "MISSING_RELEASE_ARTIFACT",
                "Expected release artifact is missing.",
                paths=missing_artifacts,
            )
        )

    clean = not dirty_paths
    release_allowed = not blockers
    return {
        "schema_version": RELEASE_CHECK_SCHEMA,
        "generated_at": _now_iso(),
        "repo": str(repo_root),
        "status": "ready" if release_allowed else "blocked",
        "release_allowed": release_allowed,
        "require_clean": require_clean,
        "git": {
            "clean": clean,
            "branch": _git_text(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"]),
            "head": _git_text(repo_root, ["rev-parse", "HEAD"]),
            "dirty_count": len(dirty_paths),
            "dirty_paths": dirty_paths,
            "entries": entries,
        },
        "intent": {
            **intent,
            "cli_intended_paths": cli_intended,
            "combined_intended_paths": combined_intended,
        },
        "unexpected_dirty_paths": unexpected_dirty_paths,
        "release_blockers": blockers,
        "warnings": warnings,
        "summary": (
            "Release allowed: git tree is clean."
            if release_allowed
            else "Release blocked: fix blockers before tagging."
        ),
    }


def format_release_check(report: Dict[str, Any]) -> str:
    lines = [
        f"Dhee release check: {report.get('status')}",
        f"  repo      {report.get('repo') or ''}",
    ]
    git = report.get("git") or {}
    if git:
        head = (git.get("head") or "")[:12]
        lines.append(f"  branch    {git.get('branch') or '(unknown)'} {head}")
        lines.append(f"  clean     {'yes' if git.get('clean') else 'no'} ({git.get('dirty_count', 0)} dirty path(s))")
    intent = report.get("intent") or {}
    intended = intent.get("combined_intended_paths") or []
    if intended:
        lines.append(f"  intent    {', '.join(intended)}")
    elif git.get("dirty_count"):
        lines.append("  intent    none")
    blockers = report.get("release_blockers") or []
    if blockers:
        lines.append("  blockers")
        for blocker in blockers:
            lines.append(f"    - {blocker.get('code')}: {blocker.get('message')}")
            paths = blocker.get("paths") or []
            if paths:
                preview = ", ".join(str(path) for path in paths[:8])
                suffix = f" (+{len(paths) - 8} more)" if len(paths) > 8 else ""
                lines.append(f"      paths: {preview}{suffix}")
    warnings = report.get("warnings") or []
    if warnings:
        lines.append("  warnings")
        for warning in warnings:
            lines.append(f"    - {warning.get('code')}: {warning.get('message')}")
    lines.append(f"  verdict   {'release allowed' if report.get('release_allowed') else 'do not tag'}")
    return "\n".join(lines)
