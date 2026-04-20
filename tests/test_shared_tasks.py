from __future__ import annotations

import os

from dhee.core.handoff_snapshot import build_handoff_snapshot
from dhee.db.sqlite import SQLiteManager
from dhee.router import handlers


def test_shared_task_round_trip_and_prune(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    task = db.upsert_shared_task(
        {
            "user_id": "default",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "title": "Collaborate on router slice",
            "status": "active",
            "created_by": "pytest",
        }
    )
    assert task["title"] == "Collaborate on router slice"

    result_id = db.save_shared_task_result(
        {
            "shared_task_id": task["id"],
            "result_key": "key-1",
            "user_id": "default",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "packet_kind": "routed_read",
            "tool_name": "Read",
            "result_status": "completed",
            "source_event_id": "read-1",
            "source_path": str(tmp_path / "file.txt"),
            "ptr": "R-123",
            "digest": "<dhee_read>digest</dhee_read>",
            "metadata": {"line_count": 10},
        }
    )
    assert result_id
    rows = db.list_shared_task_results(shared_task_id=task["id"], limit=10)
    assert len(rows) == 1
    assert rows[0]["result_status"] == "completed"

    assert db.close_shared_task(task["id"], user_id="default", prune_results=True) is True
    closed = db.get_shared_task(task["id"], user_id="default")
    assert closed is not None
    assert closed["status"] == "completed"
    assert db.list_shared_task_results(shared_task_id=task["id"], limit=10) == []


def test_handoff_includes_active_shared_task(tmp_path, monkeypatch):
    db = SQLiteManager(str(tmp_path / "history.db"))
    task = db.upsert_shared_task(
        {
            "user_id": "default",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "title": "Shared repo task",
            "status": "active",
        }
    )
    db.save_shared_task_result(
        {
            "shared_task_id": task["id"],
            "result_key": "result-1",
            "user_id": "default",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "packet_kind": "artifact_parsed",
            "tool_name": "Artifact",
            "result_status": "completed",
            "source_event_id": "artifact-1",
            "source_path": str(tmp_path / "paper.pdf"),
            "artifact_id": "artifact-1",
            "digest": "Parsed paper.pdf into portable chunks.",
            "metadata": {"chunk_count": 2},
        }
    )
    monkeypatch.setattr(
        "dhee.core.thread_state.get_last_session",
        lambda **_: {
            "id": "sess-handoff",
            "task_summary": "Resume here",
            "todos": ["continue work"],
        },
    )

    snapshot = build_handoff_snapshot(db, user_id="default", repo=str(tmp_path))
    assert snapshot["shared_task"]["title"] == "Shared repo task"
    assert snapshot["shared_task_results"][0]["packet_kind"] == "artifact_parsed"
    assert any("shared task:" in hint for hint in snapshot["resume_hints"])


def test_routed_read_publishes_claim_and_completed_result(tmp_path, monkeypatch):
    dhee_dir = tmp_path / "dhee"
    monkeypatch.setenv("DHEE_DATA_DIR", str(dhee_dir))
    monkeypatch.setenv("DHEE_USER_ID", "default")
    monkeypatch.setenv("DHEE_AGENT_ID", "codex")
    monkeypatch.setenv("DHEE_HARNESS", "codex")
    monkeypatch.setenv("DHEE_ROUTER_PTR_DIR", str(tmp_path / "ptr"))
    monkeypatch.setenv("DHEE_ROUTER_SESSION_ID", "pytest")

    db = SQLiteManager(str(dhee_dir / "history.db"))
    task = db.upsert_shared_task(
        {
            "user_id": "default",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "title": "Shared reads",
            "status": "active",
        }
    )

    seen_claims = []

    def _capture_claim(db_obj, **kwargs):
        seen_claims.append(kwargs)
        return None

    monkeypatch.setattr(handlers, "publish_in_flight", _capture_claim)
    monkeypatch.setattr(handlers, "_ROUTE_DB", None)
    monkeypatch.setattr(handlers, "_ROUTE_DB_PATH", None)

    target = tmp_path / "module.py"
    target.write_text("def demo():\n    return 1\n", encoding="utf-8")

    result = handlers.handle_dhee_read({"file_path": str(target)})
    assert result["ptr"].startswith("R-")
    assert seen_claims
    assert seen_claims[0]["packet_kind"] == "routed_read"

    rows = db.list_shared_task_results(shared_task_id=task["id"], limit=10)
    assert len(rows) == 1
    assert rows[0]["result_status"] == "completed"
    assert rows[0]["tool_name"] == "Read"
    assert rows[0]["ptr"] == result["ptr"]
