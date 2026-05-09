"""Regression coverage for OSS 6.1 collaboration and hook behavior.

Production guarantees covered:
- (a) ``close_stale_shared_tasks`` + ``shared_task_snapshot`` repo filter
- (b) ``edit_ledger.summarise`` filters: /tmp purge, 6h window, repo+session
- (c) ``handle_user_prompt`` per-turn relevance gate on the <shared> block
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dhee.core.shared_tasks import (
    _close_stale_active_tasks,
    _task_matches_repo,
    shared_task_snapshot,
)
from dhee.db.sqlite import SQLiteManager
from dhee.router import edit_ledger


# ---------------------------------------------------------------------------
# (a) Stale shared-task auto-close + repo filter
# ---------------------------------------------------------------------------


def _backdate_task(db: SQLiteManager, task_id: str, hours_ago: float) -> None:
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE shared_tasks SET updated_at = ? WHERE id = ?",
            (when, task_id),
        )


def test_close_stale_shared_tasks_drops_old_active_rows(tmp_path):
    db = SQLiteManager(str(tmp_path / "h.db"))
    fresh = db.upsert_shared_task(
        {"user_id": "u1", "title": "fresh", "status": "active",
         "repo": str(tmp_path), "workspace_id": str(tmp_path)}
    )
    stale = db.upsert_shared_task(
        {"user_id": "u1", "title": "stale", "status": "active",
         "repo": str(tmp_path), "workspace_id": str(tmp_path)}
    )
    _backdate_task(db, stale["id"], hours_ago=48)

    closed = db.close_stale_shared_tasks(user_id="u1", max_age_hours=24)
    assert closed == 1

    assert db.get_shared_task(fresh["id"], user_id="u1")["status"] == "active"
    assert db.get_shared_task(stale["id"], user_id="u1")["status"] == "closed"


def test_close_stale_shared_tasks_no_op_when_nothing_old(tmp_path):
    db = SQLiteManager(str(tmp_path / "h.db"))
    db.upsert_shared_task(
        {"user_id": "u1", "title": "fresh", "status": "active",
         "repo": str(tmp_path)}
    )
    assert db.close_stale_shared_tasks(user_id="u1", max_age_hours=24) == 0


def test_close_stale_active_tasks_helper_handles_missing_method(tmp_path):
    # Defensive: a partially-stubbed DB without close_stale_shared_tasks
    # must not crash the snapshot path.
    fake = SimpleNamespace()
    assert _close_stale_active_tasks(fake, user_id="u1") == 0


def test_shared_task_snapshot_calls_auto_close(tmp_path):
    db = SQLiteManager(str(tmp_path / "h.db"))
    stale = db.upsert_shared_task(
        {"user_id": "u1", "title": "old codex paper", "status": "active",
         "repo": str(tmp_path), "workspace_id": str(tmp_path)}
    )
    _backdate_task(db, stale["id"], hours_ago=48)

    snap = shared_task_snapshot(db, user_id="u1", repo=str(tmp_path))
    assert snap["task"] is None
    assert db.get_shared_task(stale["id"], user_id="u1")["status"] == "closed"


def test_shared_task_snapshot_drops_foreign_repo_task(tmp_path):
    db = SQLiteManager(str(tmp_path / "h.db"))
    other = tmp_path / "other_repo"
    other.mkdir()
    db.upsert_shared_task(
        {"user_id": "u1", "title": "task on other repo", "status": "active",
         "repo": str(other), "workspace_id": str(other)}
    )
    snap = shared_task_snapshot(db, user_id="u1", repo=str(tmp_path / "active_repo"))
    assert snap["task"] is None


def test_shared_task_snapshot_keeps_matching_repo_task(tmp_path):
    db = SQLiteManager(str(tmp_path / "h.db"))
    repo = tmp_path / "myrepo"
    repo.mkdir()
    task = db.upsert_shared_task(
        {"user_id": "u1", "title": "active here", "status": "active",
         "repo": str(repo), "workspace_id": str(repo)}
    )
    snap = shared_task_snapshot(db, user_id="u1", repo=str(repo))
    assert snap["task"] is not None
    assert snap["task"]["id"] == task["id"]


def test_task_matches_repo_helper():
    repo = "/Users/x/proj"
    sub = "/Users/x/proj/services/api"
    other = "/Users/x/elsewhere"
    task_here = {"repo": repo, "workspace_id": repo}
    task_sub = {"repo": sub, "workspace_id": sub}
    task_other = {"repo": other, "workspace_id": other}

    assert _task_matches_repo(task_here, repo=repo)
    assert _task_matches_repo(task_sub, repo=repo)        # subdir matches parent
    assert _task_matches_repo(task_here, repo=sub)        # parent matches subdir
    assert not _task_matches_repo(task_other, repo=repo)
    # No candidates → no constraint to apply
    assert _task_matches_repo(task_other) is True


# ---------------------------------------------------------------------------
# (b) Edit ledger filters
# ---------------------------------------------------------------------------


def _write_ledger(tmp_path, rows):
    ledger = tmp_path / "edits.jsonl"
    with ledger.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return tmp_path


def _row(path, *, at=None, s="", cwd="", t="Edit", h="abc", n=10):
    return {"t": t, "p": path, "h": h, "n": n,
            "at": at if at is not None else time.time(),
            "s": s, "cwd": cwd}


def test_summarise_drops_tmp_paths(tmp_path):
    sdir = _write_ledger(tmp_path, [
        _row("/tmp/scratch.py"),
        _row("/private/tmp/other.py"),
        _row(str(tmp_path / "real.py"), cwd=str(tmp_path)),
    ])
    out = edit_ledger.summarise(session_dir=sdir, repo=str(tmp_path))
    paths = {e.path for e in out}
    assert paths == {str(tmp_path / "real.py")}


def test_summarise_drops_old_entries(tmp_path):
    long_ago = time.time() - 12 * 3600  # 12h ago
    sdir = _write_ledger(tmp_path, [
        _row(str(tmp_path / "old.py"), at=long_ago, cwd=str(tmp_path)),
        _row(str(tmp_path / "new.py"), cwd=str(tmp_path)),
    ])
    out = edit_ledger.summarise(session_dir=sdir, repo=str(tmp_path))
    paths = {e.path for e in out}
    assert paths == {str(tmp_path / "new.py")}


def test_summarise_repo_filter(tmp_path):
    other_cwd = str(tmp_path.parent / "other_proj")
    sdir = _write_ledger(tmp_path, [
        _row("/Users/x/proj/file.py", cwd="/Users/x/proj"),
        _row("/Users/x/elsewhere/file.py", cwd="/Users/x/elsewhere"),
    ])
    out = edit_ledger.summarise(session_dir=sdir, repo="/Users/x/proj")
    assert {e.path for e in out} == {"/Users/x/proj/file.py"}


def test_summarise_session_filter(tmp_path):
    sdir = _write_ledger(tmp_path, [
        _row(str(tmp_path / "a.py"), s="sess-keep", cwd=str(tmp_path)),
        _row(str(tmp_path / "b.py"), s="sess-drop", cwd=str(tmp_path)),
    ])
    out = edit_ledger.summarise(
        session_dir=sdir, session_id="sess-keep", repo=str(tmp_path)
    )
    assert {e.path for e in out} == {str(tmp_path / "a.py")}


def test_summarise_backward_compat_for_unsessioned_rows(tmp_path):
    # Older rows have no "s" or "cwd" — they should still surface when
    # within the freshness window.
    row = {"t": "Edit", "p": str(tmp_path / "legacy.py"),
           "h": "x", "n": 1, "at": time.time()}
    sdir = _write_ledger(tmp_path, [row])
    out = edit_ledger.summarise(
        session_dir=sdir, session_id="any", repo=str(tmp_path)
    )
    assert {e.path for e in out} == {str(tmp_path / "legacy.py")}


def test_record_persists_session_and_cwd(tmp_path, monkeypatch):
    # Point the ledger writer at our tmp dir.
    monkeypatch.setattr(edit_ledger, "_session_dir", lambda: tmp_path)
    monkeypatch.setenv("DHEE_SESSION_ID", "sess-X")
    edit_ledger.record("Edit", str(tmp_path / "f.py"), "hello")

    log = (tmp_path / "edits.jsonl").read_text().strip().splitlines()
    assert len(log) == 1
    rec = json.loads(log[0])
    assert rec["s"] == "sess-X"
    assert rec["cwd"]
    assert rec["p"] == str(tmp_path / "f.py")


# ---------------------------------------------------------------------------
# (c) Per-turn relevance gate on the <shared> block
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Returns embeddings driven by simple keyword presence."""

    _vocab = ["paper", "research", "router", "error", "git", "log", "code"]

    def embed(self, text: str, *, memory_action: str = "search"):
        t = text.lower()
        return [1.0 if w in t else 0.0 for w in self._vocab]


def _fake_dhee():
    fake_memory = SimpleNamespace(embedder=_FakeEmbedder())
    return SimpleNamespace(memory=fake_memory)


def test_shared_block_relevance_gate_drops_unrelated():
    from dhee.hooks.claude_code.__main__ import _shared_block_is_relevant

    dhee = _fake_dhee()
    shared = {
        "task": {"title": "i have asked codex to read a research paper"},
        "results": [{"digest": "paper opened, 12 chunks indexed"}],
    }
    # Prompt has nothing to do with research papers. Even with the fake
    # embedder spuriously matching "code" against "codex", cosine stays
    # below the production threshold.
    assert not _shared_block_is_relevant(
        dhee, "why still this error in router code?", shared
    )


def test_shared_block_relevance_gate_keeps_related():
    from dhee.hooks.claude_code.__main__ import _shared_block_is_relevant

    dhee = _fake_dhee()
    shared = {
        "task": {"title": "router error investigation"},
        "results": [{"digest": "git log inspected for router changes"}],
    }
    assert _shared_block_is_relevant(
        dhee, "why still this router error in git log?", shared
    )


def test_shared_block_relevance_gate_handles_missing_task():
    from dhee.hooks.claude_code.__main__ import _shared_block_is_relevant

    assert not _shared_block_is_relevant(_fake_dhee(), "anything", {"task": None})


def test_shared_block_relevance_gate_fails_closed_on_embedder_error():
    from dhee.hooks.claude_code.__main__ import _shared_block_is_relevant

    class _Boom:
        def embed(self, *a, **k):
            raise RuntimeError("embedder offline")

    dhee = SimpleNamespace(memory=SimpleNamespace(embedder=_Boom()))
    shared = {"task": {"title": "x"}, "results": []}
    assert not _shared_block_is_relevant(dhee, "anything", shared)
