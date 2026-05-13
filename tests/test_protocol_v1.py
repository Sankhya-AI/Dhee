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


def _export_basic_pack(tmp_path: Path, memory: str = "Portable security fixture.") -> Path:
    source = _make_dhee(tmp_path / "source")
    source.remember(memory)
    pack_path = tmp_path / "portable.dheemem"
    export_pack(
        db=source._engram.memory.db,
        vector_store=source._engram.memory.vector_store,
        output_path=pack_path,
        user_id="default",
        key_dir=tmp_path / "keys",
    )
    return pack_path


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
    dry_run = import_pack(
        db=target_memory.db,
        vector_store=target_memory.vector_store,
        input_path=pack_path,
        user_id="default",
        strategy="dry-run",
    )
    assert dry_run["handoff_bootstrap"]["format"] == "dhee_handoff"
    assert "recent_artifacts" in dry_run["handoff_bootstrap"]

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
    assert import_result["handoff_bootstrap"]["format"] == "dhee_handoff"

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


def test_dheemem_roundtrip_preserves_repo_shared_context(tmp_path):
    from dhee import repo_link

    source = _make_dhee(tmp_path / "source")
    source_memory = source._engram.memory
    source.remember("Repo context portability fixture.")

    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    repo_link.add_entry(
        source_repo,
        kind="decision",
        title="Run replay gates",
        content="Release branches must pass dhee router gate before merge.",
        meta={"scope": "release"},
    )

    pack_path = tmp_path / "repo-context.dheemem"
    export_result = export_pack(
        db=source_memory.db,
        vector_store=source_memory.vector_store,
        output_path=pack_path,
        user_id="default",
        key_dir=tmp_path / "keys",
        repo=source_repo,
    )
    assert export_result["counts"]["repo_context_entries"] == 1

    inspected = inspect_pack(pack_path)
    assert inspected["repo_context"]["format"] == "dhee_repo_context"
    assert inspected["repo_context"]["records"] == 1

    target = _make_dhee(tmp_path / "target")
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    dry_run = import_pack(
        db=target._engram.memory.db,
        vector_store=target._engram.memory.vector_store,
        input_path=pack_path,
        user_id="default",
        strategy="dry-run",
        repo=target_repo,
    )
    assert dry_run["repo_context"]["records"] == 1
    assert not (target_repo / ".dhee").exists()

    result = import_pack(
        db=target._engram.memory.db,
        vector_store=target._engram.memory.vector_store,
        input_path=pack_path,
        user_id="default",
        strategy="merge",
        repo=target_repo,
    )
    assert result["repo_context_import"]["imported"] == 1
    entries = repo_link.list_entries(target_repo)
    assert len(entries) == 1
    assert entries[0].title == "Run replay gates"
    assert "router gate" in entries[0].content

    second = import_pack(
        db=target._engram.memory.db,
        vector_store=target._engram.memory.vector_store,
        input_path=pack_path,
        user_id="default",
        strategy="merge",
        repo=target_repo,
    )
    assert second["repo_context_import"]["imported"] == 0
    assert second["repo_context_import"]["skipped_existing"] == 1


def test_dheemem_rejects_manifest_tampering(tmp_path):
    pack_path = _export_basic_pack(tmp_path, "Manifest tampering must be rejected.")
    tampered_path = tmp_path / "manifest-tampered.dheemem"

    with ZipFile(pack_path, "r") as src, ZipFile(tampered_path, "w", compression=ZIP_DEFLATED) as dst:
        for info in src.infolist():
            raw = src.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(raw.decode("utf-8"))
                manifest["user_id"] = "attacker"
                raw = json.dumps(manifest, sort_keys=True).encode("utf-8")
            dst.writestr(info, raw)

    with pytest.raises(ValueError, match="Manifest signature verification failed"):
        inspect_pack(tampered_path)


def test_dheemem_rejects_missing_required_handoff(tmp_path):
    pack_path = _export_basic_pack(tmp_path, "Missing handoff must be rejected.")
    missing_path = tmp_path / "missing-handoff.dheemem"

    with ZipFile(pack_path, "r") as src, ZipFile(missing_path, "w", compression=ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename == "handoff.json":
                continue
            dst.writestr(info, src.read(info.filename))

    with pytest.raises(ValueError, match="missing required files: handoff.json"):
        inspect_pack(missing_path)


def test_dheemem_rejects_unexpected_archive_member(tmp_path):
    pack_path = _export_basic_pack(tmp_path, "Unexpected archive member must be rejected.")
    extra_path = tmp_path / "extra-member.dheemem"

    with ZipFile(pack_path, "r") as src, ZipFile(extra_path, "w", compression=ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr("extra.json", b"{}")

    with pytest.raises(ValueError, match="unexpected files: extra.json"):
        inspect_pack(extra_path)


def test_dheemem_rejects_path_traversal_archive_member(tmp_path):
    pack_path = _export_basic_pack(tmp_path, "Path traversal member must be rejected.")
    traversal_path = tmp_path / "traversal.dheemem"

    with ZipFile(pack_path, "r") as src, ZipFile(traversal_path, "w", compression=ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr("../escape.json", b"{}")

    with pytest.raises(ValueError, match=r"Unsafe archive path in pack: \.\./escape\.json"):
        inspect_pack(traversal_path)


def test_dheemem_rejects_tampered_repo_context_payload(tmp_path):
    from dhee import repo_link

    source = _make_dhee(tmp_path / "source")
    source.remember("Repo context tamper fixture.")
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    repo_link.add_entry(
        source_repo,
        kind="learning",
        title="Stable pointer",
        content="Use pointer-backed evidence for handoffs.",
    )

    pack_path = tmp_path / "repo-context.dheemem"
    export_pack(
        db=source._engram.memory.db,
        vector_store=source._engram.memory.vector_store,
        output_path=pack_path,
        user_id="default",
        key_dir=tmp_path / "keys",
        repo=source_repo,
    )

    tampered_path = tmp_path / "repo-context-tampered.dheemem"
    with ZipFile(pack_path, "r") as src, ZipFile(tampered_path, "w", compression=ZIP_DEFLATED) as dst:
        for info in src.infolist():
            raw = src.read(info.filename)
            if info.filename == "repo_context/entries.jsonl":
                row = json.loads(raw.decode("utf-8").splitlines()[0])
                row["content"] = "A malicious edit after signing."
                raw = (json.dumps(row) + "\n").encode("utf-8")
            dst.writestr(info, raw)

    target = _make_dhee(tmp_path / "target")
    with pytest.raises(ValueError, match="Hash mismatch for repo_context/entries.jsonl"):
        import_pack(
            db=target._engram.memory.db,
            vector_store=target._engram.memory.vector_store,
            input_path=tampered_path,
            user_id="default",
            strategy="merge",
            repo=tmp_path / "target-repo",
        )


def test_dheemem_rejects_repo_context_symlink_escape(tmp_path):
    source = _make_dhee(tmp_path / "source")
    source.remember("Symlink escape fixture.")
    repo = tmp_path / "repo"
    context_dir = repo / ".dhee" / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "manifest.json").write_text('{"schema_version":1}\n', encoding="utf-8")
    secret_target = tmp_path / "outside.jsonl"
    secret_target.write_text('{"id":"x","content":"outside"}\n', encoding="utf-8")
    (context_dir / "entries.jsonl").symlink_to(secret_target)

    with pytest.raises(ValueError, match="Repo context file is unsafe"):
        export_pack(
            db=source._engram.memory.db,
            vector_store=source._engram.memory.vector_store,
            output_path=tmp_path / "unsafe.dheemem",
            user_id="default",
            key_dir=tmp_path / "keys",
            repo=repo,
        )


def test_dheemem_rejects_target_repo_context_symlink_on_import(tmp_path):
    from dhee import repo_link

    source = _make_dhee(tmp_path / "source")
    source.remember("Target symlink fixture.")
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    repo_link.add_entry(
        source_repo,
        kind="learning",
        title="No symlink imports",
        content="Repo context imports must stay under the target repo.",
    )
    pack_path = tmp_path / "source.dheemem"
    export_pack(
        db=source._engram.memory.db,
        vector_store=source._engram.memory.vector_store,
        output_path=pack_path,
        user_id="default",
        key_dir=tmp_path / "keys",
        repo=source_repo,
    )

    target = _make_dhee(tmp_path / "target")
    target_repo = tmp_path / "target-repo"
    outside = tmp_path / "outside-context"
    outside.mkdir()
    (target_repo / ".dhee").mkdir(parents=True)
    (target_repo / ".dhee" / "context").symlink_to(outside)

    with pytest.raises(ValueError, match="Target repo context directory is unsafe"):
        import_pack(
            db=target._engram.memory.db,
            vector_store=target._engram.memory.vector_store,
            input_path=pack_path,
            user_id="default",
            strategy="merge",
            repo=target_repo,
        )


def test_dheemem_rejects_repo_context_secret_payload(tmp_path):
    from dhee import repo_link

    source = _make_dhee(tmp_path / "source")
    source.remember("Secret rejection fixture.")
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_link.add_entry(
        repo,
        kind="note",
        title="Bad shared context",
        content="api_key=abcdefghijklmnopqrstuvwxyz123456",
    )

    with pytest.raises(ValueError, match="likely secret"):
        export_pack(
            db=source._engram.memory.db,
            vector_store=source._engram.memory.vector_store,
            output_path=tmp_path / "secret.dheemem",
            user_id="default",
            key_dir=tmp_path / "keys",
            repo=repo,
        )


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
