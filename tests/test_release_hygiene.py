from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dhee.release_hygiene import (
    RELEASE_INTENT_REL_PATH,
    load_release_intent,
    release_check,
    write_release_intent,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _codes(report: dict) -> set[str]:
    return {str(item.get("code")) for item in report.get("release_blockers") or []}


def test_release_check_allows_clean_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    report = release_check(repo)

    assert report["status"] == "ready"
    assert report["release_allowed"] is True
    assert report["git"]["clean"] is True
    assert report["release_blockers"] == []


def test_release_check_blocks_dirty_repo_without_intent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("# changed\n", encoding="utf-8")

    report = release_check(repo)

    assert report["status"] == "blocked"
    assert report["release_allowed"] is False
    assert "GIT_WORKTREE_DIRTY" in _codes(report)
    assert "UNEXPECTED_DIRTY_PATHS" in _codes(report)
    assert report["unexpected_dirty_paths"] == ["README.md"]
    assert (report["warnings"] or [])[0]["code"] == "RELEASE_INTENT_MISSING"


def test_release_intent_documents_scope_but_does_not_allow_dirty_release(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    result = write_release_intent(repo, ["README.md"], reason="docs hardening")
    assert result["ok"] is True
    _git(repo, "add", RELEASE_INTENT_REL_PATH)
    _git(repo, "commit", "-q", "-m", "add release intent")

    (repo / "README.md").write_text("# changed\n", encoding="utf-8")
    report = release_check(repo)

    assert report["status"] == "blocked"
    assert report["release_allowed"] is False
    assert "GIT_WORKTREE_DIRTY" in _codes(report)
    assert "UNEXPECTED_DIRTY_PATHS" not in _codes(report)
    assert report["unexpected_dirty_paths"] == []
    assert report["intent"]["combined_intended_paths"] == ["README.md"]


def test_release_check_still_blocks_unexpected_paths_when_clean_requirement_relaxed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    result = write_release_intent(repo, ["README.md"])
    assert result["ok"] is True
    _git(repo, "add", RELEASE_INTENT_REL_PATH)
    _git(repo, "commit", "-q", "-m", "add release intent")

    (repo / "README.md").write_text("# changed\n", encoding="utf-8")
    (repo / "surprise.py").write_text("print('surprise')\n", encoding="utf-8")
    report = release_check(repo, require_clean=False)

    assert report["status"] == "blocked"
    assert "GIT_WORKTREE_DIRTY" not in _codes(report)
    assert "UNEXPECTED_DIRTY_PATHS" in _codes(report)
    assert report["unexpected_dirty_paths"] == ["surprise.py"]


def test_corrupt_release_intent_is_a_blocker(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    intent_path = repo / RELEASE_INTENT_REL_PATH
    intent_path.parent.mkdir(parents=True, exist_ok=True)
    intent_path.write_text("{bad json", encoding="utf-8")

    intent = load_release_intent(repo)
    report = release_check(repo)

    assert intent["ok"] is False
    assert "RELEASE_INTENT_UNREADABLE" in _codes(report)
    assert any(
        diagnostic.get("code") == "RUNTIME_JSON_CORRUPT"
        for diagnostic in report["intent"]["diagnostics"]
    )


def test_release_cli_json_blocks_dirty_tree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("# changed\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "dhee.cli",
            "release",
            "check",
            "--repo",
            str(repo),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["status"] == "blocked"
    assert "GIT_WORKTREE_DIRTY" in _codes(payload)
