"""Tests for the agent-activity → workspace information line pipeline.

Covers the core promise of PR 1: every agent tool-call lands on the shared
information line exactly once, addressable by workspace/project, with
dedup on retries.
"""

from __future__ import annotations

import os

from dhee.core import shared_tasks
from dhee.core.workspace_line import emit_agent_activity, resolve_workspace_and_project
from dhee.db.sqlite import SQLiteManager


def _seed_workspace(db: SQLiteManager, *, root_path: str, user_id: str = "default") -> tuple[str, str]:
    """Seed a workspace + one project rooted at ``root_path``. Returns (ws_id, project_id)."""
    workspace = db.upsert_workspace(
        {
            "user_id": user_id,
            "name": "Sankhya AI Labs",
            "description": "primary",
            "root_path": root_path,
        }
    )
    project = db.upsert_workspace_project(
        {
            "user_id": user_id,
            "workspace_id": workspace["id"],
            "name": "backend",
            "description": "backend project",
        }
    )
    return workspace["id"], project["id"]


# ---------------------------------------------------------------------------
# resolve_workspace_and_project
# ---------------------------------------------------------------------------


def test_resolve_prefers_agent_session_over_path(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    ws_id, project_id = _seed_workspace(db, root_path=str(tmp_path))
    db.upsert_agent_session(
        {
            "user_id": "default",
            "runtime_id": "claude-code",
            "native_session_id": "native-1",
            "title": "live session",
            "workspace_id": ws_id,
            "project_id": project_id,
        }
    )

    resolved_ws, resolved_project = resolve_workspace_and_project(
        db,
        runtime_id="claude-code",
        native_session_id="native-1",
        repo="/nonexistent/other/repo",
    )
    assert resolved_ws == ws_id
    assert resolved_project == project_id


def test_resolve_falls_back_to_path_match(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    ws_id, _ = _seed_workspace(db, root_path=str(tmp_path))

    nested = tmp_path / "src" / "module.py"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("x = 1\n", encoding="utf-8")

    resolved_ws, project_id = resolve_workspace_and_project(
        db,
        cwd=str(tmp_path),
        source_path=str(nested),
    )
    assert resolved_ws == ws_id
    assert project_id is None  # no agent session, no shared-task project


def test_resolve_no_match_returns_none(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    _seed_workspace(db, root_path=str(tmp_path / "projectA"))

    resolved_ws, project_id = resolve_workspace_and_project(
        db,
        cwd="/totally/unrelated/path",
    )
    assert resolved_ws is None
    assert project_id is None


# ---------------------------------------------------------------------------
# emit_agent_activity
# ---------------------------------------------------------------------------


def test_emit_writes_line_message(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    ws_id, project_id = _seed_workspace(db, root_path=str(tmp_path))
    db.upsert_agent_session(
        {
            "user_id": "default",
            "runtime_id": "claude-code",
            "native_session_id": "native-1",
            "title": "live",
            "workspace_id": ws_id,
            "project_id": project_id,
        }
    )

    row = emit_agent_activity(
        db,
        tool_name="Read",
        packet_kind="hook_post_tool",
        digest="file contents digest",
        runtime_id="claude-code",
        native_session_id="native-1",
        cwd=str(tmp_path),
        source_path=str(tmp_path / "README.md"),
        source_event_id="tool-use-1",
        ptr="R-abc",
        harness="claude-code",
        agent_id="claude-code",
    )
    assert row is not None
    assert row["workspace_id"] == ws_id
    assert row["project_id"] == project_id
    assert row["channel"] == "project"
    assert row["message_kind"] == "tool.hook_post_tool"
    assert row["title"].startswith("read · README.md")

    messages = db.list_workspace_line_messages(workspace_id=ws_id, user_id="default")
    assert len(messages) == 1
    assert messages[0]["id"] == row["id"]


def test_emit_is_idempotent_on_retry(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    ws_id, project_id = _seed_workspace(db, root_path=str(tmp_path))

    common = dict(
        tool_name="Bash",
        packet_kind="routed_bash",
        digest="exit=0 stdout=hello",
        cwd=str(tmp_path),
        source_event_id="bash-evt-1",
        ptr="B-001",
        harness="codex",
        runtime_id="codex",
        native_session_id="codex-sess-1",
    )

    first = emit_agent_activity(db, **common)
    assert first is not None

    second = emit_agent_activity(db, **common)
    assert second is None  # dedup_key collided → silent drop

    messages = db.list_workspace_line_messages(workspace_id=ws_id, user_id="default")
    assert len(messages) == 1


def test_emit_no_workspace_resolved_is_silent(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    # No workspace seeded for this path.
    row = emit_agent_activity(
        db,
        tool_name="Read",
        packet_kind="hook_post_tool",
        digest="anything",
        cwd="/no/such/workspace",
        source_path="/no/such/workspace/file.txt",
        runtime_id="claude-code",
        native_session_id="native-x",
    )
    assert row is None


def test_emit_distinct_events_are_not_deduped(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    ws_id, _ = _seed_workspace(db, root_path=str(tmp_path))

    first = emit_agent_activity(
        db,
        tool_name="Read",
        packet_kind="hook_post_tool",
        digest="a",
        cwd=str(tmp_path),
        source_path=str(tmp_path / "a.txt"),
        source_event_id="evt-1",
        ptr="R-a",
    )
    second = emit_agent_activity(
        db,
        tool_name="Read",
        packet_kind="hook_post_tool",
        digest="b",
        cwd=str(tmp_path),
        source_path=str(tmp_path / "b.txt"),
        source_event_id="evt-2",
        ptr="R-b",
    )
    assert first is not None
    assert second is not None
    messages = db.list_workspace_line_messages(workspace_id=ws_id, user_id="default")
    assert len(messages) == 2


def test_human_published_messages_coexist_with_agent_emitted(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    ws_id, _ = _seed_workspace(db, root_path=str(tmp_path))

    human = db.add_workspace_line_message(
        {
            "workspace_id": ws_id,
            "user_id": "default",
            "channel": "workspace",
            "message_kind": "note",
            "title": "heads up",
            "body": "contract changed",
        }
    )
    assert human is not None
    assert human["message_kind"] == "note"

    agent = emit_agent_activity(
        db,
        tool_name="Read",
        packet_kind="hook_post_tool",
        digest="agent output",
        cwd=str(tmp_path),
        source_path=str(tmp_path / "spec.md"),
        source_event_id="evt-42",
    )
    assert agent is not None

    messages = db.list_workspace_line_messages(workspace_id=ws_id, user_id="default")
    kinds = {m["message_kind"] for m in messages}
    assert "note" in kinds
    assert "tool.hook_post_tool" in kinds


# ---------------------------------------------------------------------------
# shared_tasks → line pipeline (end-to-end PR 1 promise)
# ---------------------------------------------------------------------------


def test_publish_shared_task_result_fans_out_to_line(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    ws_id, project_id = _seed_workspace(db, root_path=str(tmp_path))

    task = db.upsert_shared_task(
        {
            "user_id": "default",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "project_id": project_id,
            "title": "PR demo",
            "status": "active",
        }
    )

    result = shared_tasks.publish_shared_task_result(
        db,
        packet_kind="routed_read",
        tool_name="Read",
        digest="<dhee_read>hello</dhee_read>",
        repo=str(tmp_path),
        cwd=str(tmp_path),
        source_path=str(tmp_path / "paper.pdf"),
        source_event_id="evt-pdf",
        ptr="R-pdf",
        shared_task_id=task["id"],
        harness="codex",
        agent_id="codex",
        session_id="codex-sess",
    )
    assert result is not None

    messages = db.list_workspace_line_messages(workspace_id=ws_id, user_id="default")
    assert len(messages) == 1
    msg = messages[0]
    assert msg["workspace_id"] == ws_id
    assert msg["project_id"] == project_id
    assert msg["channel"] == "project"
    assert msg["message_kind"] == "tool.routed_read"
    meta = msg["metadata"] or {}
    assert meta.get("ptr") == "R-pdf"
    assert meta.get("harness") == "codex"
    assert meta.get("result_status") == "completed"

    # Retry — same event/ptr — must not create a second message.
    shared_tasks.publish_shared_task_result(
        db,
        packet_kind="routed_read",
        tool_name="Read",
        digest="<dhee_read>hello</dhee_read>",
        repo=str(tmp_path),
        cwd=str(tmp_path),
        source_path=str(tmp_path / "paper.pdf"),
        source_event_id="evt-pdf",
        ptr="R-pdf",
        shared_task_id=task["id"],
        harness="codex",
        session_id="codex-sess",
    )
    messages = db.list_workspace_line_messages(workspace_id=ws_id, user_id="default")
    assert len(messages) == 1


def test_emit_survives_missing_shared_task_context(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    ws_id, _ = _seed_workspace(db, root_path=str(tmp_path))

    # No shared task, no agent session — path-based resolution alone.
    row = emit_agent_activity(
        db,
        tool_name="Edit",
        packet_kind="hook_post_tool",
        digest="edited 1 line",
        cwd=str(tmp_path),
        source_path=str(tmp_path / "main.py"),
        source_event_id="edit-1",
        harness="claude-code",
    )
    assert row is not None
    assert row["workspace_id"] == ws_id
    assert row["channel"] == "workspace"
