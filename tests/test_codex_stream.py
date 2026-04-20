from __future__ import annotations

import json

from dhee.core.artifacts import ArtifactManager
from dhee.core.codex_stream import sync_latest_codex_stream
from dhee.db.sqlite import SQLiteManager


def test_codex_stream_sync_publishes_shared_results_and_artifacts(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    manager = ArtifactManager(db)

    task = db.upsert_shared_task(
        {
            "user_id": "default",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "title": "Shared codex task",
            "status": "active",
            "created_by": "codex",
        }
    )
    assert task["title"] == "Shared codex task"

    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4 codex stream bytes")
    log_path = tmp_path / "codex.jsonl"
    entries = [
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-1",
                "arguments": json.dumps(
                    {"cmd": f"python parse.py {paper}", "cwd": str(tmp_path)}
                ),
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "exec_command_end",
                "call_id": "call-1",
                "cwd": str(tmp_path),
                "parsed_cmd": [{"type": "read", "path": str(paper)}],
                "aggregated_output": "Parsed paper content should become shared and durable.",
                "command": ["/bin/zsh", "-lc", f"python parse.py {paper}"],
                "exit_code": 0,
            },
        },
    ]
    with log_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")

    stats = sync_latest_codex_stream(
        manager,
        db,
        user_id="default",
        log_path=str(log_path),
    )
    assert stats["claims"] == 1
    assert stats["completed"] == 1
    assert stats["attached"] >= 1
    assert stats["parsed"] == 1

    rows = db.list_shared_task_results(shared_task_id=task["id"], limit=10)
    assert len(rows) >= 2
    assert any(row["result_status"] == "in_flight" for row in rows)
    assert any(row["result_status"] == "completed" for row in rows)
    assert any(row["harness"] == "codex" for row in rows)

    # Cursor makes re-sync idempotent for already-read bytes.
    again = sync_latest_codex_stream(
        manager,
        db,
        user_id="default",
        log_path=str(log_path),
    )
    assert again["logs"] == 0
