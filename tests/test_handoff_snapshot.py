from __future__ import annotations

import json
from pathlib import Path

from dhee.core.artifacts import ArtifactManager
from dhee.core.handoff_snapshot import build_handoff_snapshot
from dhee.core.intention import IntentionStore
from dhee.core.task_state import TaskStateStore
from dhee.db.sqlite import SQLiteManager


def test_build_handoff_snapshot_includes_real_state(tmp_path, monkeypatch):
    data_dir = tmp_path / "dhee-data"
    monkeypatch.setenv("DHEE_DATA_DIR", str(data_dir))
    db = SQLiteManager(str(tmp_path / "history.db"))

    db.add_memory(
        {
            "id": "mem-1",
            "memory": "User prefers concise updates.",
            "user_id": "default",
            "metadata": {"source_type": "user"},
            "categories": ["preference"],
            "content_hash": "hash-1",
        }
    )

    paper = tmp_path / "handoff.pdf"
    paper.write_bytes(b"%PDF-1.4 handoff bytes")
    ArtifactManager(db).capture_host_parse(
        path=str(paper),
        extracted_text="Portable handoff should mention reusable uploaded assets.",
        user_id="default",
        cwd=str(tmp_path),
        harness="claude_code",
        extraction_source="claude_read",
    )

    intention_store = IntentionStore(data_dir=str(data_dir / "intentions"))
    intention_store.store(
        user_id="default",
        description="Run tests after auth edits",
        trigger_keywords=["auth", "tests"],
        action_payload="Run tests",
    )

    task_store = TaskStateStore(data_dir=str(data_dir / "tasks"))
    task = task_store.create_task(
        user_id="default",
        goal="Ship portable handoff",
        task_type="architecture",
        plan=["add handoff.json", "verify new machine restore"],
    )
    task.start()
    task_store.update_task(task)

    monkeypatch.setattr(
        "dhee.core.thread_state.get_last_session",
        lambda **_: {
            "id": "sess-1",
            "agent_id": "codex",
            "repo": str(tmp_path),
            "status": "active",
            "task_summary": "Implemented portability slice.",
            "todos": ["add handoff snapshot"],
            "files_touched": ["dhee/protocol/v1.py"],
            "source": "bus_session",
        },
    )

    snapshot = build_handoff_snapshot(db, user_id="default", repo=str(tmp_path))
    assert snapshot["format"] == "dhee_handoff"
    assert snapshot["continuity_source"] == "last_session"
    assert snapshot["last_session"]["task_summary"] == "Implemented portability slice."
    assert snapshot["tasks"]["active"]["goal"] == "Ship portable handoff"
    assert snapshot["intentions"][0]["action_payload"] == "Run tests"
    assert snapshot["recent_memories"][0]["memory"] == "User prefers concise updates."
    assert snapshot["recent_artifacts"][0]["filename"] == "handoff.pdf"
    assert snapshot["resume_hints"]


def test_handoff_filters_fixture_memories_from_recent_context(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    db = SQLiteManager(str(tmp_path / "history.db"))
    db.add_memory(
        {
            "id": "fixture-memory",
            "memory": "Test memory",
            "user_id": "default",
            "source_app": "test_app",
            "content_hash": "fixture-hash",
        }
    )
    db.add_memory(
        {
            "id": "real-memory",
            "memory": "Use Dhee compiled state before re-reading old transcript.",
            "user_id": "default",
            "metadata": {"source_type": "agent"},
            "content_hash": "real-hash",
        }
    )
    monkeypatch.setattr("dhee.core.thread_state.get_last_session", lambda **_: None)

    snapshot = build_handoff_snapshot(db, user_id="default", repo=str(tmp_path), memory_limit=5)

    assert [row["id"] for row in snapshot["recent_memories"]] == ["real-memory"]
    assert snapshot["continuity_hygiene"]["filtered_recent_memory_count"] == 1
    assert snapshot["continuity_hygiene"]["filtered_recent_memory_reasons"]["suppressed:test_fixture"] == 1


def test_handoff_filters_legacy_placeholder_memories_from_recent_context(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    db = SQLiteManager(str(tmp_path / "history.db"))
    for memory_id, text in (
        ("memory-one", "Memory one"),
        ("default-user-memory", "Default user memory"),
        ("hashed-python", "I like Python 14cdcdeb"),
    ):
        db.add_memory(
            {
                "id": memory_id,
                "memory": text,
                "user_id": "default",
                "content_hash": f"{memory_id}-hash",
            }
        )
    db.add_memory(
        {
            "id": "real-memory",
            "memory": "Chotu assistant decisions must be recalled with evidence.",
            "user_id": "default",
            "metadata": {"source_type": "agent"},
            "content_hash": "real-memory-hash",
        }
    )
    monkeypatch.setattr("dhee.core.thread_state.get_last_session", lambda **_: None)

    snapshot = build_handoff_snapshot(db, user_id="default", repo=str(tmp_path), memory_limit=5)

    assert [row["id"] for row in snapshot["recent_memories"]] == ["real-memory"]
    assert snapshot["continuity_hygiene"]["filtered_recent_memory_count"] == 3


def test_handoff_filters_operational_transport_memories(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    db = SQLiteManager(str(tmp_path / "history.db"))
    db.add_memory(
        {
            "id": "edit-event",
            "memory": "edited /src/auth.py",
            "user_id": "default",
            "metadata": {"kind": "file_touched", "tool": "Edit", "success": True},
            "content_hash": "edit-event-hash",
        }
    )
    db.add_memory(
        {
            "id": "real-memory",
            "memory": "Chotu assistant decisions must be recalled with evidence.",
            "user_id": "default",
            "metadata": {"source_type": "agent"},
            "content_hash": "real-memory-hash",
        }
    )
    monkeypatch.setattr("dhee.core.thread_state.get_last_session", lambda **_: None)

    snapshot = build_handoff_snapshot(db, user_id="default", repo=str(tmp_path), memory_limit=5)

    assert [row["id"] for row in snapshot["recent_memories"]] == ["real-memory"]
    assert snapshot["continuity_hygiene"]["filtered_recent_memory_reasons"]["suppressed:operational_event"] == 1


def test_handoff_survives_legacy_scalar_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    db = SQLiteManager(str(tmp_path / "history.db"))
    db.add_memory(
        {
            "id": "legacy-scalar",
            "memory": "Chotu should preserve user goals as canonical context.",
            "user_id": "default",
            "metadata": {"source_type": "agent"},
            "content_hash": "legacy-scalar-hash",
        }
    )
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE memories SET metadata = ? WHERE id = ?",
            (json.dumps("legacy metadata string"), "legacy-scalar"),
        )
    monkeypatch.setattr("dhee.core.thread_state.get_last_session", lambda **_: None)

    memory = db.get_memory("legacy-scalar")
    assert memory["metadata"]["legacy_metadata_raw"] == "legacy metadata string"

    snapshot = build_handoff_snapshot(db, user_id="default", repo=str(tmp_path), memory_limit=5)

    assert snapshot["format"] == "dhee_handoff"
    assert snapshot["recent_memories"][0]["id"] == "legacy-scalar"
