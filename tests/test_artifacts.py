from __future__ import annotations

import json
from pathlib import Path

from dhee import Dhee
from dhee.core.artifacts import ArtifactManager
from dhee.core.codex_artifacts import ingest_codex_session_log


def _make_dhee(tmp_path: Path) -> Dhee:
    return Dhee(
        provider="mock",
        data_dir=tmp_path,
        user_id="default",
        in_memory=True,
        auto_context=False,
        auto_checkpoint=False,
    )


def test_artifact_capture_dedup_and_prompt_reuse(tmp_path):
    dhee = _make_dhee(tmp_path / "src")
    manager = ArtifactManager(dhee._engram.memory.db, engram=dhee._engram)

    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4 fake pdf bytes")
    extracted = "\n\n".join(
        [
            ("MemoSight compresses reasoning spans into reusable memory tokens. " * 6).strip(),
            ("The paper also emphasizes compact reusable context for future turns. " * 6).strip(),
            ("Critical surface routing should keep durable knowledge inside memory. " * 6).strip(),
            ("Folder-local artifact reuse matters for developer workflows. " * 6).strip(),
            ("Portable handoff depends on preserving extracted artifact chunks. " * 6).strip(),
            ("Reflection without raw replay is the core savings mechanism. " * 6).strip(),
            ("Reusing only the best chunk should be cheaper than replaying the entire extraction. " * 6).strip(),
            ("This paragraph exists to ensure the fake artifact spans multiple chunks in tests. " * 6).strip(),
        ]
    )

    attached = manager.attach(str(paper), user_id="default", cwd=str(tmp_path))
    assert attached is not None
    assert attached["lifecycle_state"] == "attached"
    assert attached["bindings"][0]["workspace_id"] == str(tmp_path.resolve())

    first = manager.capture_host_parse(
        path=str(paper),
        extracted_text=extracted,
        user_id="default",
        cwd=str(tmp_path),
        harness="claude_code",
        extraction_source="claude_read",
    )
    assert first is not None
    assert first["created"] is True
    assert first["chunk_count"] >= 1
    assert first["indexed_count"] >= 1

    second = manager.capture_host_parse(
        path=str(paper),
        extracted_text=extracted,
        user_id="default",
        cwd=str(tmp_path),
        harness="claude_code",
        extraction_source="claude_read",
    )
    assert second is not None
    assert second["created"] is False

    artifact = dhee._engram.memory.db.get_artifact(first["artifact_id"])
    assert artifact is not None
    assert artifact["lifecycle_state"] == "portable"
    assert len(artifact["extractions"]) == 1
    assert len(artifact["chunks"]) >= 1

    indexed = [
        row for row in dhee._engram.memory.db.get_all_memories(user_id="default", limit=1000)
        if (row.get("metadata") or {}).get("kind") == "artifact_chunk"
    ]
    assert indexed, "artifact chunks should also be indexed as memories"

    prompt_matches = manager.prompt_matches(
        f"Use {paper} and explain reusable context",
        user_id="default",
        cwd=str(tmp_path),
        limit=1,
    )
    assert prompt_matches
    assert "MemoSight" in prompt_matches[0].text

    route_summary = dhee._engram.memory.db.summarize_route_decisions(user_id="default")
    assert route_summary["by_packet_kind"]["artifact_parse"] >= 2
    assert route_summary["by_packet_kind"]["artifact_reuse"] >= 1
    assert route_summary["by_route"]["refract_memory"] >= 1
    assert route_summary["artifact_reuse_saved_tokens"] > 0


def test_artifact_export_import_preserves_chunks(tmp_path):
    source = _make_dhee(tmp_path / "source")
    source_manager = ArtifactManager(source._engram.memory.db, engram=source._engram)

    paper = tmp_path / "portable.pdf"
    paper.write_bytes(b"%PDF-1.4 portability bytes")
    extracted = "Portable memory means the extracted paper content survives a new machine."

    result = source_manager.capture_host_parse(
        path=str(paper),
        extracted_text=extracted,
        user_id="default",
        cwd=str(tmp_path),
        harness="claude_code",
        extraction_source="claude_read",
    )
    assert result is not None

    payload = source_manager.export_payload(user_id="default")
    assert payload["artifacts_manifest"]
    assert payload["artifact_extractions"]
    assert payload["artifact_chunks"]

    target = _make_dhee(tmp_path / "target")
    target_manager = ArtifactManager(target._engram.memory.db, engram=target._engram)
    stats = target_manager.import_payload(payload, user_id="default")
    assert stats["artifacts"] == 1
    assert stats["chunks"] >= 1

    prompt_matches = target_manager.prompt_matches(
        "Summarize portable.pdf",
        user_id="default",
        cwd=str(tmp_path),
    )
    assert prompt_matches
    assert "new machine" in prompt_matches[0].text


def test_codex_session_ingest_captures_attachment_and_parse(tmp_path):
    dhee = _make_dhee(tmp_path / "codex-store")
    manager = ArtifactManager(dhee._engram.memory.db, engram=dhee._engram)

    paper = tmp_path / "session-paper.pdf"
    paper.write_bytes(b"%PDF-1.4 codex bytes")
    extracted = "Codex transcript ingestion stores host parsed output after the first successful read."

    log_path = tmp_path / "session.jsonl"
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
                "call_id": "call-1",
                "cwd": str(tmp_path),
                "parsed_cmd": [{"type": "read", "path": str(paper)}],
                "aggregated_output": extracted,
                "command": ["/bin/zsh", "-lc", f"python read {paper}"],
            },
        },
    ]
    with log_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")

    stats = ingest_codex_session_log(manager, str(log_path), user_id="default")
    assert stats["attached"] >= 1
    assert stats["parsed"] == 1
    assert stats["chunks_indexed"] >= 1

    artifacts = dhee._engram.memory.db.list_artifacts(user_id="default", limit=10)
    assert len(artifacts) == 1
    artifact = dhee._engram.memory.db.get_artifact(artifacts[0]["artifact_id"])
    assert artifact is not None
    assert artifact["lifecycle_state"] == "portable"
    assert artifact["bindings"][0]["harness"] == "codex"
