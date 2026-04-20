from __future__ import annotations

import json
from pathlib import Path

import pytest

mcp_server = pytest.importorskip("dhee.mcp_server", reason="mcp package not installed")

from dhee.core.artifacts import ArtifactManager
from dhee.db.sqlite import SQLiteManager


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db = SQLiteManager(str(tmp_path / "history.db"))
    monkeypatch.setattr(mcp_server, "_db", db)
    monkeypatch.setattr(mcp_server, "get_db", lambda: db)
    return db


def test_dhee_list_assets_returns_compact_summaries(tmp_path, temp_db):
    manager = ArtifactManager(temp_db)
    paper = tmp_path / "manual.pdf"
    paper.write_bytes(b"%PDF-1.4 mcp bytes")
    manager.capture_host_parse(
        path=str(paper),
        extracted_text="Portable artifact memory for repeated use.",
        user_id="default",
        cwd=str(tmp_path),
        harness="claude_code",
        extraction_source="claude_read",
    )

    result = mcp_server._handle_dhee_list_assets(None, {"user_id": "default"})
    assert result["count"] == 1
    row = result["results"][0]
    assert row["filename"] == "manual.pdf"
    assert row["lifecycle_state"] == "portable"
    assert row["extraction_count"] == 1
    assert "content_hash" in row


def test_dhee_get_asset_omits_bodies_by_default_and_can_include_chunks(tmp_path, temp_db):
    manager = ArtifactManager(temp_db)
    paper = tmp_path / "detail.pdf"
    paper.write_bytes(b"%PDF-1.4 detail bytes")
    stored = manager.capture_host_parse(
        path=str(paper),
        extracted_text="This extracted body should stay hidden unless explicitly requested.\n\nChunk two.",
        user_id="default",
        cwd=str(tmp_path),
        harness="claude_code",
        extraction_source="claude_read",
    )
    assert stored is not None

    summary = mcp_server._handle_dhee_get_asset(None, {"artifact_id": stored["artifact_id"]})
    assert summary["artifact_id"] == stored["artifact_id"]
    assert summary["chunk_count"] >= 1
    assert "extracted_text" not in summary["extractions"][0]
    assert summary["chunks"] == []

    detailed = mcp_server._handle_dhee_get_asset(
        None,
        {
            "artifact_id": stored["artifact_id"],
            "include_extraction_text": True,
            "include_chunks": True,
            "chunk_limit": 2,
        },
    )
    assert "extracted_text" in detailed["extractions"][0]
    assert detailed["chunks"]
    assert "content" in detailed["chunks"][0]


def test_dhee_sync_codex_artifacts_ingests_log(tmp_path, temp_db):
    paper = tmp_path / "session.pdf"
    paper.write_bytes(b"%PDF-1.4 codex sync bytes")

    log_path = tmp_path / "codex.jsonl"
    entries = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Files mentioned by the user:\n\n## paper: {paper}",
                    }
                ],
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "exec_command_end",
                "call_id": "call-sync-1",
                "cwd": str(tmp_path),
                "parsed_cmd": [{"type": "read", "path": str(paper)}],
                "aggregated_output": "Codex host parse should become durable memory.",
                "command": ["/bin/zsh", "-lc", f"python read {paper}"],
            },
        },
    ]
    with log_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")

    result = mcp_server._handle_dhee_sync_codex_artifacts(
        None,
        {"user_id": "default", "log_path": str(log_path)},
    )
    assert result["parsed"] == 1
    assert result["attached"] >= 1

    listed = mcp_server._handle_dhee_list_assets(None, {"user_id": "default"})
    assert listed["count"] == 1
    asset_id = listed["results"][0]["artifact_id"]
    detail = mcp_server._handle_dhee_get_asset(
        None,
        {"artifact_id": asset_id, "include_extraction_text": True},
    )
    assert detail["bindings"][0]["harness"] == "codex"
    assert "durable memory" in detail["extractions"][0]["extracted_text"]


def test_dhee_why_explains_memory_and_artifact(tmp_path, temp_db):
    manager = ArtifactManager(temp_db)
    paper = tmp_path / "why.pdf"
    paper.write_bytes(b"%PDF-1.4 why bytes")
    stored = manager.capture_host_parse(
        path=str(paper),
        extracted_text="Why surfaces should explain artifact-backed memory.",
        user_id="default",
        cwd=str(tmp_path),
        harness="claude_code",
        extraction_source="claude_read",
    )
    assert stored is not None

    memories = temp_db.get_all_memories(user_id="default", limit=50)
    artifact_chunk = next(
        row for row in memories if (row.get("metadata") or {}).get("kind") == "artifact_chunk"
    )
    temp_db.add_distillation_provenance(
        semantic_memory_id=artifact_chunk["id"],
        episodic_memory_ids=[artifact_chunk["id"]],
        run_id="why-run-1",
    )

    explained_memory = mcp_server._handle_dhee_why(
        None,
        {"identifier": artifact_chunk["id"], "history_limit": 5},
    )
    assert explained_memory["kind"] == "memory"
    assert explained_memory["artifact"]["artifact_id"] == stored["artifact_id"]
    assert explained_memory["history_count"] >= 1
    assert explained_memory["distillation"]["source_count"] == 1

    explained_artifact = mcp_server._handle_dhee_why(
        None,
        {
            "identifier": stored["artifact_id"],
            "include_extraction_text": True,
            "include_chunks": True,
            "chunk_limit": 1,
        },
    )
    assert explained_artifact["kind"] == "artifact"
    assert explained_artifact["artifact_id"] == stored["artifact_id"]
    assert "artifact-backed memory" in explained_artifact["extractions"][0]["extracted_text"]
    assert explained_artifact["chunks"]


def test_dhee_handoff_returns_structured_snapshot(tmp_path, temp_db, monkeypatch):
    paper = tmp_path / "handoff-mcp.pdf"
    paper.write_bytes(b"%PDF-1.4 handoff mcp bytes")
    ArtifactManager(temp_db).capture_host_parse(
        path=str(paper),
        extracted_text="The handoff should include recent artifacts and memories.",
        user_id="default",
        cwd=str(tmp_path),
        harness="claude_code",
        extraction_source="claude_read",
    )
    temp_db.add_memory(
        {
            "id": "mem-handoff",
            "memory": "Keep the handoff compact and structured.",
            "user_id": "default",
            "metadata": {"source_type": "user"},
            "categories": ["policy"],
            "content_hash": "handoff-hash",
        }
    )
    monkeypatch.setattr(
        mcp_server,
        "_default_user_id",
        lambda args: "default",
    )
    monkeypatch.setattr(
        "dhee.core.handoff_snapshot.get_last_session",
        lambda **_: {"id": "sess-handoff", "task_summary": "Resume here", "todos": ["continue work"]},
    )

    result = mcp_server._handle_dhee_handoff(None, {"repo": str(tmp_path)})
    assert result["format"] == "dhee_handoff"
    assert result["last_session"]["task_summary"] == "Resume here"
    assert result["recent_memories"]
    assert result["recent_artifacts"][0]["filename"] == "handoff-mcp.pdf"


def test_dhee_shared_task_create_results_and_close(tmp_path, temp_db):
    created = mcp_server._handle_dhee_shared_task(
        None,
        {
            "action": "create",
            "user_id": "default",
            "repo": str(tmp_path),
            "title": "Shared MCP task",
        },
    )
    assert created["title"] == "Shared MCP task"

    temp_db.save_shared_task_result(
        {
            "shared_task_id": created["id"],
            "result_key": "mcp-shared-1",
            "user_id": "default",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "packet_kind": "routed_grep",
            "tool_name": "Grep",
            "result_status": "completed",
            "source_event_id": "grep-1",
            "source_path": str(tmp_path),
            "ptr": "G-123",
            "digest": "<dhee_grep>shared digest</dhee_grep>",
            "metadata": {"match_count": 2},
        }
    )

    results = mcp_server._handle_dhee_shared_task_results(
        None,
        {"user_id": "default", "repo": str(tmp_path)},
    )
    assert results["shared_task"]["title"] == "Shared MCP task"
    assert results["count"] == 1
    assert results["results"][0]["tool_name"] == "Grep"

    closed = mcp_server._handle_dhee_shared_task(
        None,
        {"action": "close", "user_id": "default", "repo": str(tmp_path)},
    )
    assert closed["closed"] is True
    post_close = mcp_server._handle_dhee_shared_task_results(
        None,
        {"user_id": "default", "repo": str(tmp_path)},
    )
    assert post_close["status"] == "not_found"


def test_codex_shared_task_results_auto_syncs_log(tmp_path, temp_db):
    created = mcp_server._handle_dhee_shared_task(
        None,
        {
            "action": "create",
            "user_id": "default",
            "repo": str(tmp_path),
            "title": "Codex shared task",
        },
    )
    paper = tmp_path / "codex-shared.pdf"
    paper.write_bytes(b"%PDF-1.4 shared")
    log_path = tmp_path / "codex-shared.jsonl"
    entries = [
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "codex-call-1",
                "arguments": json.dumps({"cmd": f"cat {paper}", "cwd": str(tmp_path)}),
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "exec_command_end",
                "call_id": "codex-call-1",
                "cwd": str(tmp_path),
                "parsed_cmd": [{"type": "read", "path": str(paper)}],
                "aggregated_output": "auto synced shared output",
                "command": ["/bin/zsh", "-lc", f"cat {paper}"],
                "exit_code": 0,
            },
        },
    ]
    with log_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")

    results = mcp_server._handle_dhee_shared_task_results(
        None,
        {
            "user_id": "default",
            "repo": str(tmp_path),
            "harness": "codex",
            "log_path": str(log_path),
        },
    )
    assert results["shared_task"]["id"] == created["id"]
    assert results["count"] >= 1
    assert any(row["harness"] == "codex" for row in results["results"])
