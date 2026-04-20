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
