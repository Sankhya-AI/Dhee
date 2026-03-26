"""Repository identity helpers for cross-agent handoff."""

from __future__ import annotations

import hashlib
import os
import subprocess
from typing import Dict, Optional


def _run_git(repo_path: str, args: list[str]) -> Optional[str]:
    try:
        output = subprocess.check_output(
            ["git", "-C", repo_path, *args],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        value = (output or "").strip()
        return value or None
    except Exception:
        return None


def _normalize_remote(remote_url: Optional[str]) -> Optional[str]:
    if not remote_url:
        return None
    value = remote_url.strip()
    if not value:
        return None
    if value.startswith("git@"):
        # git@github.com:owner/repo.git -> ssh://git@github.com/owner/repo
        value = value.replace(":", "/", 1)
        value = f"ssh://{value}"
    if value.endswith(".git"):
        value = value[:-4]
    return value.lower()


def _hash(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:20]}"


def canonicalize_repo_identity(
    repo_path: Optional[str],
    *,
    branch: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Return stable repo identity for handoff lane routing."""
    path_hint = repo_path or os.getcwd()
    resolved_path = os.path.realpath(os.path.expanduser(path_hint))

    git_root = _run_git(resolved_path, ["rev-parse", "--show-toplevel"])
    canonical_path = os.path.realpath(git_root) if git_root else resolved_path

    git_remote = _normalize_remote(_run_git(canonical_path, ["config", "--get", "remote.origin.url"]))
    git_branch = branch or _run_git(canonical_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if git_branch == "HEAD":
        git_branch = None

    if git_remote:
        repo_id = _hash("git", git_remote)
    else:
        repo_id = _hash("path", canonical_path.lower())

    return {
        "repo_id": repo_id,
        "repo_path": canonical_path,
        "branch": git_branch,
        "remote": git_remote,
    }

