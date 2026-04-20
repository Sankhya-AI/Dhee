from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from dhee import Dhee
from dhee.core.artifacts import ArtifactManager
from dhee.core.provenance import explain_memory
from dhee.protocol import export_pack, import_pack, inspect_pack


def _make_dhee(tmp_path: Path) -> Dhee:
    return Dhee(
        provider="mock",
        data_dir=tmp_path,
        user_id="default",
        in_memory=True,
        auto_context=False,
        auto_checkpoint=False,
    )


def test_dheemem_roundtrip_preserves_memories_vectors_and_artifacts(tmp_path):
    source = _make_dhee(tmp_path / "source")
    source_memory = source._engram.memory
    add_result = source.remember("User strongly prefers concise architecture writeups.")
    source_id = add_result["id"]
    episodic_id = source.remember("Episodic source for later synthesis.")["id"]
    source_memory.db.add_distillation_provenance(
        semantic_memory_id=source_id,
        episodic_memory_ids=[episodic_id],
        run_id="distill-run-1",
    )

    paper = tmp_path / "notes.pdf"
    paper.write_bytes(b"%PDF-1.4 protocol bytes")
    manager = ArtifactManager(source_memory.db, engram=source._engram)
    parsed = manager.capture_host_parse(
        path=str(paper),
        extracted_text="Portable packs should restore vector nodes and artifact knowledge.",
        user_id="default",
        cwd=str(tmp_path),
        harness="claude_code",
        extraction_source="claude_read",
    )
    assert parsed is not None

    pack_path = tmp_path / "portable.dheemem"
    export_result = export_pack(
        db=source_memory.db,
        vector_store=source_memory.vector_store,
        output_path=pack_path,
        user_id="default",
        key_dir=tmp_path / "keys",
    )
    assert pack_path.exists()
    assert export_result["counts"]["memories"] >= 2
    assert export_result["counts"]["vectors"] >= 2

    manifest = inspect_pack(pack_path)
    assert manifest["format"] == "dheemem"
    assert manifest["version"] == "1"
    assert manifest["handoff"]["format"] == "dhee_handoff"
    assert "recent_artifacts" in manifest["handoff"]

    target = _make_dhee(tmp_path / "target")
    target_memory = target._engram.memory
    import_result = import_pack(
        db=target_memory.db,
        vector_store=target_memory.vector_store,
        input_path=pack_path,
        user_id="default",
        strategy="merge",
    )
    assert import_result["memory_import"]["imported"] >= 2
    assert import_result["vectors_imported"] >= 2
    assert import_result["artifact_import"]["artifacts"] == 1

    restored = target_memory.db.get_memory(source_id)
    assert restored is not None
    assert restored["memory"] == "User strongly prefers concise architecture writeups."

    explanation = explain_memory(target_memory.db, source_id)
    assert explanation["history_count"] >= 1
    assert explanation["distillation"]["source_count"] == 1

    second_import = import_pack(
        db=target_memory.db,
        vector_store=target_memory.vector_store,
        input_path=pack_path,
        user_id="default",
        strategy="merge",
    )
    assert second_import["memory_import"]["imported"] == 0
    assert second_import["history_imported"] == 0

    results = target_memory.search(
        query="concise architecture",
        user_id="default",
        limit=5,
    )["results"]
    assert any(r["id"] == source_id for r in results)

    target_manager = ArtifactManager(target_memory.db, engram=target._engram)
    matches = target_manager.prompt_matches(
        "Summarize notes.pdf",
        user_id="default",
        cwd=str(tmp_path),
    )
    assert matches
    assert "restore vector nodes" in matches[0].text


def test_dheemem_tamper_detection_rejects_modified_payload(tmp_path):
    source = _make_dhee(tmp_path / "source")
    source.remember("Tamper detection should reject modified packs.")

    pack_path = tmp_path / "tamper.dheemem"
    export_pack(
        db=source._engram.memory.db,
        vector_store=source._engram.memory.vector_store,
        output_path=pack_path,
        user_id="default",
        key_dir=tmp_path / "keys",
    )

    tampered_path = tmp_path / "tampered.dheemem"
    with ZipFile(pack_path, "r") as src, ZipFile(tampered_path, "w", compression=ZIP_DEFLATED) as dst:
        for info in src.infolist():
            raw = src.read(info.filename)
            if info.filename == "memories.jsonl":
                rows = raw.decode("utf-8").splitlines()
                first = json.loads(rows[0])
                first["memory"] = "This pack was tampered with after export."
                raw = (json.dumps(first) + "\n").encode("utf-8")
            dst.writestr(info, raw)

    target = _make_dhee(tmp_path / "target")
    with pytest.raises(ValueError, match="Hash mismatch"):
        import_pack(
            db=target._engram.memory.db,
            vector_store=target._engram.memory.vector_store,
            input_path=tampered_path,
            user_id="default",
            strategy="merge",
        )
