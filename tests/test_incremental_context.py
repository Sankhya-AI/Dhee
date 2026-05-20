import json

import pytest

from dhee.incremental_context import (
    ContextExtractor,
    DependencyCycleError,
    DuplicateTargetError,
    ExtractedTarget,
    FileContextStore,
    IncrementalContextEngine,
    MissingDependencyError,
    SourceRecord,
    manifest_from_json,
    manifest_to_json,
    stable_hash,
    verify_context_manifest,
)


def _fact_targets(source, context):
    words = sorted(str(source.content).lower().split())
    return [
        ExtractedTarget(
            id=f"fact:{source.id}",
            kind="fact",
            payload={"source": source.id, "words": words},
            metadata={"shape": "word-list"},
        )
    ]


def _summary_targets(source, context):
    return [
        ExtractedTarget(
            id=f"summary:{source.id}",
            kind="summary",
            payload={"label": source.id},
            source_ids=[],
            dependencies=[f"fact:{source.id}"],
        )
    ]


def _engine(fact_version="1"):
    return IncrementalContextEngine(
        [
            ContextExtractor("facts", fact_version, _fact_targets),
            ContextExtractor("summaries", "1", _summary_targets),
        ]
    )


def _source(content="Alpha beta", **kwargs):
    return SourceRecord(
        "doc-a",
        content,
        user_id=kwargs.pop("user_id", "u1"),
        privacy_scope=kwargs.pop("privacy_scope", "workspace"),
        source_ref=kwargs.pop("source_ref", "file://doc-a.md"),
        **kwargs,
    )


def _rewrite_manifest_hash(manifest):
    manifest["manifest_hash"] = stable_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    return manifest


def _write_manifest(path, manifest):
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _issue_codes(result):
    return {issue["code"] for issue in result["issues"]}


def test_store_rebuild_reload_then_unchanged_rebuild(tmp_path):
    engine = _engine()
    store = FileContextStore(tmp_path / "ctx")
    sources = [_source(metadata={"path": "doc-a.md"})]

    first = engine.rebuild_to_store(sources, store)
    loaded = engine.load_manifest(store)
    second = engine.rebuild_to_store(sources, store)

    assert store.manifest_path.exists()
    assert len(list(store.targets_dir.glob("*.json"))) == 2
    assert list(store.root.glob("**/*.tmp")) == []
    assert loaded == first["manifest"]
    assert second["dirty"]["changed"] is False
    assert second["stats"]["written_target_file_count"] == 0
    assert second["stats"]["skipped_target_file_count"] == 2
    assert second["dirty"]["unchanged_targets"] == ["fact:doc-a", "summary:doc-a"]
    assert engine.verify_store(sources, store)["ok"] is True

    round_tripped = manifest_from_json(manifest_to_json(first["manifest"]))
    assert round_tripped == first["manifest"]
    assert verify_context_manifest(sources, engine.extractors.values(), first["manifest"])["ok"] is True


def test_changed_source_dirty_propagates_and_updates_payload_file(tmp_path):
    engine = _engine()
    store = FileContextStore(tmp_path / "ctx")
    first = engine.rebuild_to_store([_source("Alpha beta")], store)
    fact_path = store.target_path("fact:doc-a", first["manifest"]["targets"]["fact:doc-a"])
    before = json.loads(fact_path.read_text(encoding="utf-8"))

    rebuilt = engine.rebuild_to_store([_source("Alpha gamma")], store)
    after = json.loads(fact_path.read_text(encoding="utf-8"))

    assert before["payload"]["words"] == ["alpha", "beta"]
    assert after["payload"]["words"] == ["alpha", "gamma"]
    assert "payload_changed" in rebuilt["dirty"]["targets"]["fact:doc-a"]["reasons"]
    assert "dependency_changed:fact:doc-a" in rebuilt["dirty"]["targets"]["summary:doc-a"]["reasons"]
    assert "dependency_dirty:fact:doc-a" in rebuilt["dirty"]["targets"]["summary:doc-a"]["reasons"]
    assert rebuilt["stats"]["written_target_file_count"] == 2
    assert engine.verify_store([_source("Alpha gamma")], store)["ok"] is True


def test_extractor_version_dirty_propagates_through_target_edges(tmp_path):
    store = FileContextStore(tmp_path / "ctx")
    original_engine = _engine(fact_version="1")
    original_engine.rebuild_to_store([_source("Alpha beta")], store)

    upgraded_engine = _engine(fact_version="2")
    rebuilt = upgraded_engine.rebuild_to_store([_source("Alpha beta")], store)

    assert "extractor_version_changed:1->2" in rebuilt["dirty"]["targets"]["fact:doc-a"]["reasons"]
    assert "dependency_dirty:fact:doc-a" in rebuilt["dirty"]["targets"]["summary:doc-a"]["reasons"]
    assert rebuilt["stats"]["written_target_file_count"] == 2
    assert upgraded_engine.verify_store([_source("Alpha beta")], store)["ok"] is True


def test_plan_is_dry_run_and_matches_rebuild_dirty_decision(tmp_path):
    engine = _engine()
    store = FileContextStore(tmp_path / "ctx")
    first = engine.rebuild_to_store([_source("Alpha beta")], store)

    planned = engine.plan([_source("Alpha gamma")], previous_manifest=first["manifest"])
    files_before = sorted(path.name for path in store.targets_dir.glob("*.json"))
    rebuilt = engine.rebuild_to_store([_source("Alpha gamma")], store)
    files_after = sorted(path.name for path in store.targets_dir.glob("*.json"))

    assert planned["dirty"] == rebuilt["dirty"]
    assert files_before == files_after
    assert planned["stats"]["changed_target_count"] == 2


def test_verify_store_detects_missing_target_payload(tmp_path):
    engine = _engine()
    store = FileContextStore(tmp_path / "ctx")
    result = engine.rebuild_to_store([_source()], store)
    store.target_path("fact:doc-a", result["manifest"]["targets"]["fact:doc-a"]).unlink()

    verification = engine.verify_store([_source()], store)

    assert verification["ok"] is False
    assert "target_payload_missing" in _issue_codes(verification)


def test_verify_store_detects_corrupt_payload_hash(tmp_path):
    engine = _engine()
    store = FileContextStore(tmp_path / "ctx")
    result = engine.rebuild_to_store([_source()], store)
    entry = result["manifest"]["targets"]["fact:doc-a"]
    path = store.target_path("fact:doc-a", entry)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["words"] = ["tampered"]
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    verification = engine.verify_store([_source()], store)

    assert verification["ok"] is False
    assert "target_payload_hash_mismatch" in _issue_codes(verification)


def test_verify_store_detects_manifest_schema_and_hash_drift(tmp_path):
    engine = _engine()
    store = FileContextStore(tmp_path / "ctx")
    result = engine.rebuild_to_store([_source()], store)
    manifest = dict(result["manifest"])
    manifest["schema_version"] = "dhee.incremental_context.v0"
    _write_manifest(store.manifest_path, manifest)

    verification = engine.verify_store([_source()], store)

    assert verification["ok"] is False
    codes = _issue_codes(verification)
    assert "unsupported_schema_version" in codes
    assert "manifest_hash_mismatch" in codes


def test_verify_store_detects_manifest_graph_corruption(tmp_path):
    engine = _engine()
    store = FileContextStore(tmp_path / "ctx")
    result = engine.rebuild_to_store([_source()], store)
    manifest = json.loads(json.dumps(result["manifest"]))
    manifest["dependency_edges"].append(dict(manifest["dependency_edges"][0]))
    manifest["dependency_edges"].append(
        {
            "downstream": "fact:doc-a",
            "extractor": "tamper",
            "kind": "source",
            "upstream": "missing-source",
        }
    )
    manifest["targets"]["fact:doc-a"]["dependencies"] = ["summary:doc-a"]
    manifest["targets"]["summary:doc-a"]["dependencies"] = ["fact:doc-a", "missing-target"]
    _write_manifest(store.manifest_path, _rewrite_manifest_hash(manifest))

    verification = engine.verify_store([_source()], store)

    assert verification["ok"] is False
    codes = _issue_codes(verification)
    assert "duplicate_dependency_edge" in codes
    assert "missing_source_reference" in codes
    assert "dangling_target_dependency" in codes
    assert "dependency_cycle" in codes


def test_duplicate_target_id_is_rejected():
    def same_target(source, context):
        return [ExtractedTarget(id="same", payload={"from": source.id})]

    engine = IncrementalContextEngine(
        [
            ContextExtractor("one", "1", same_target),
            ContextExtractor("two", "1", same_target),
        ]
    )

    with pytest.raises(DuplicateTargetError):
        engine.rebuild([_source()])


def test_dangling_target_dependency_is_rejected():
    def dangling_target(source, context):
        return [ExtractedTarget(id="dependent", payload={}, dependencies=["missing"], source_ids=[])]

    engine = IncrementalContextEngine([ContextExtractor("dangling", "1", dangling_target)])

    with pytest.raises(MissingDependencyError):
        engine.rebuild([_source()])


def test_dependency_cycle_is_rejected():
    def cyclic_targets(source, context):
        return [
            ExtractedTarget(id="a", payload={"n": 1}, dependencies=["b"], source_ids=[]),
            ExtractedTarget(id="b", payload={"n": 2}, dependencies=["a"], source_ids=[]),
        ]

    engine = IncrementalContextEngine([ContextExtractor("cyclic", "1", cyclic_targets)])

    with pytest.raises(DependencyCycleError):
        engine.rebuild([_source()])


def test_privacy_scope_is_inherited_and_store_verification_detects_mismatch(tmp_path):
    engine = _engine()
    store = FileContextStore(tmp_path / "ctx")
    result = engine.rebuild_to_store([_source(privacy_scope="private")], store)

    assert result["manifest"]["targets"]["fact:doc-a"]["privacy_scope"] == "private"
    assert result["manifest"]["targets"]["summary:doc-a"]["privacy_scope"] == "private"
    assert result["manifest"]["targets"]["summary:doc-a"]["source_refs"] == ["file://doc-a.md"]

    manifest = json.loads(json.dumps(result["manifest"]))
    manifest["targets"]["fact:doc-a"]["privacy_scope"] = "public"
    _write_manifest(store.manifest_path, _rewrite_manifest_hash(manifest))

    verification = engine.verify_store([_source(privacy_scope="private")], store)

    assert verification["ok"] is False
    assert "privacy_scope_mismatch" in _issue_codes(verification)


@pytest.mark.parametrize(
    "lifecycle",
    [
        {"redacted_at": "2026-05-20T00:00:00Z", "redaction_reason": "user_requested"},
        {"deleted_at": "2026-05-20T00:00:00Z"},
    ],
)
def test_redacted_or_deleted_source_does_not_project_active_targets(tmp_path, lifecycle):
    engine = _engine()
    store = FileContextStore(tmp_path / "ctx")
    engine.rebuild_to_store([_source("Alpha beta")], store)

    rebuilt = engine.rebuild_to_store([_source("Alpha beta", **lifecycle)], store)

    assert rebuilt["targets"] == {}
    assert rebuilt["manifest"]["targets"] == {}
    assert rebuilt["stats"]["skipped_source_count"] == 1
    assert rebuilt["stats"]["removed_target_count"] == 2
    assert list(store.targets_dir.glob("*.json")) == []
    assert engine.verify_store([_source("Alpha beta", **lifecycle)], store)["ok"] is True
