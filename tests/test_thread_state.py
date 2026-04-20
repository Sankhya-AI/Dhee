from __future__ import annotations

from dhee.core.handoff_snapshot import build_handoff_snapshot
from dhee.db.sqlite import SQLiteManager


def test_thread_state_round_trip(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))

    stored = db.upsert_thread_state(
        {
            "user_id": "default",
            "thread_id": "thread-1",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "folder_path": ".",
            "status": "active",
            "summary": "Continue router work",
            "current_goal": "Ship thread-native continuity",
            "current_step": "Wire handoff to prefer thread state",
            "session_id": "sess-123",
            "metadata": {"harness": "codex"},
        }
    )

    assert stored["thread_id"] == "thread-1"
    assert stored["summary"] == "Continue router work"

    fetched = db.get_thread_state(user_id="default", thread_id="thread-1")
    assert fetched is not None
    assert fetched["current_goal"] == "Ship thread-native continuity"
    assert fetched["metadata"]["harness"] == "codex"

    assert db.delete_thread_state(user_id="default", thread_id="thread-1") is True
    assert db.get_thread_state(user_id="default", thread_id="thread-1") is None


def test_handoff_prefers_thread_state_without_session_lookup(tmp_path, monkeypatch):
    db = SQLiteManager(str(tmp_path / "history.db"))
    db.upsert_thread_state(
        {
            "user_id": "default",
            "thread_id": "thread-42",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "status": "active",
            "summary": "We already know where this thread is",
            "current_goal": "Avoid repeated session recovery",
            "current_step": "Use thread-native continuity first",
        }
    )

    def _should_not_run(**_kwargs):
        raise AssertionError("get_last_session should not run when thread_state exists")

    monkeypatch.setattr("dhee.core.thread_state.get_last_session", _should_not_run)

    snapshot = build_handoff_snapshot(
        db,
        user_id="default",
        repo=str(tmp_path),
        thread_id="thread-42",
    )

    assert snapshot["continuity_source"] == "thread_state"
    assert snapshot["thread_state"]["current_goal"] == "Avoid repeated session recovery"
    assert snapshot["last_session"] is None
    assert any("goal:" in hint for hint in snapshot["resume_hints"])


def test_handoff_falls_back_to_last_session_when_thread_missing(tmp_path, monkeypatch):
    db = SQLiteManager(str(tmp_path / "history.db"))

    monkeypatch.setattr(
        "dhee.core.thread_state.get_last_session",
        lambda **_: {
            "id": "sess-fallback",
            "agent_id": "codex",
            "repo": str(tmp_path),
            "status": "active",
            "task_summary": "Recovered from handoff store.",
            "todos": ["finish continuity layer"],
            "source": "bus_session",
        },
    )

    snapshot = build_handoff_snapshot(
        db,
        user_id="default",
        repo=str(tmp_path),
        thread_id="missing-thread",
    )

    assert snapshot["continuity_source"] == "last_session"
    assert snapshot["thread_state"] is None
    assert snapshot["last_session"]["task_summary"] == "Recovered from handoff store."
