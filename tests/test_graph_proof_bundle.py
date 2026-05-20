from dhee.graph_proof_bundle import GRAPH_PROOF_BUNDLE_SCHEMA, build_graph_proof_bundle


def _repo_graph():
    return {
        "schema_version": "dhee.repo_graph_artifact.v1",
        "artifact_id": "repo_graph_demo",
        "repo": "/repo/demo",
        "brain_ref": "repo_brain:abc123",
        "nodes": [
            {
                "id": "file:dhee/context_firewall.py",
                "type": "file",
                "label": "dhee/context_firewall.py",
                "metadata": {"path": "dhee/context_firewall.py"},
                "provenance": {"source": "file_manifest", "evidence_pointer": "file_manifest:dhee/context_firewall.py"},
            },
            {
                "id": "file:tests/test_context_firewall.py",
                "type": "test",
                "label": "tests/test_context_firewall.py",
                "metadata": {"path": "tests/test_context_firewall.py"},
                "provenance": {"source": "file_manifest", "evidence_pointer": "file_manifest:tests/test_context_firewall.py"},
            },
            {
                "id": "symbol:allow_path",
                "type": "symbol",
                "label": "ContextFirewall.allow_path",
                "metadata": {"path": "dhee/context_firewall.py", "qualname": "ContextFirewall.allow_path"},
                "provenance": {"source": "symbols", "evidence_pointer": "symbols:allow_path"},
            },
        ],
        "edges": [
            {
                "id": "edge_contains_allow",
                "source": "file:dhee/context_firewall.py",
                "target": "symbol:allow_path",
                "type": "contains",
                "confidence": 0.99,
                "metadata": {"reason": "symbol index contains source span"},
                "provenance": {"source": "symbols", "evidence_pointer": "symbols:allow_path"},
            },
            {
                "id": "edge_tested_by",
                "source": "file:dhee/context_firewall.py",
                "target": "file:tests/test_context_firewall.py",
                "type": "tested_by",
                "confidence": 0.86,
                "metadata": {"command": "pytest tests/test_context_firewall.py", "reasons": ["test imports source module"]},
                "provenance": {"source": "test_ownership", "evidence_pointer": "test_ownership:dhee/context_firewall.py"},
            },
        ],
    }


def _context_graph():
    graph = _repo_graph()
    return {
        "format": "dhee_context_graph_query.v1",
        "context_graph": {
            "schema_version": "dhee.context_graph_slice.v1",
            "query": "Fix context firewall",
            "repo": "/repo/demo",
            "brain_ref": "repo_brain:abc123",
            "nodes": graph["nodes"],
            "edges": graph["edges"],
            "proof_items": [
                {
                    "kind": "file",
                    "id": "file:dhee/context_firewall.py",
                    "why": ["localized by goal token overlap", "owned test exists"],
                    "evidence_pointers": ["localization:dhee/context_firewall.py"],
                    "score": 0.82,
                },
                {
                    "kind": "test",
                    "id": "file:tests/test_context_firewall.py",
                    "why": ["owned test from test-ownership index"],
                    "command": "pytest tests/test_context_firewall.py",
                    "evidence_pointers": ["test_ownership:dhee/context_firewall.py"],
                    "score": 0.78,
                },
            ],
        },
    }


def test_graph_proof_bundle_attaches_context_item_to_paths_sources_and_temporal_validity():
    temporal_facts = {
        "results": [
            {
                "id": "tf_firewall",
                "subject": "dhee/context_firewall.py",
                "predicate": "validated_by",
                "object": "tests/test_context_firewall.py",
                "fact_text": "dhee/context_firewall.py is validated by tests/test_context_firewall.py.",
                "valid_from": "2026-01-01T00:00:00+00:00",
                "observed_at": "2026-01-02T00:00:00+00:00",
                "confidence": 0.9,
                "status": "active",
                "active": True,
                "source_scene": "scene_firewall",
                "source_event_ids": ["evt_firewall"],
                "source_memory_ids": ["mem_firewall"],
                "evidence": [{"ref": "test_ownership:dhee/context_firewall.py", "path": "tests/test_context_firewall.py"}],
            }
        ]
    }
    context_items = [
        {
            "context_id": "ctx_firewall",
            "kind": "localization",
            "title": "Context firewall source",
            "path": "dhee/context_firewall.py",
            "evidence_pointer": "localization:dhee/context_firewall.py",
            "why_included": "localizer selected source file",
            "confidence": 0.8,
        }
    ]

    result = build_graph_proof_bundle(
        context_items,
        repo_graph={"repo_graph": _repo_graph()},
        context_graph=_context_graph(),
        temporal_facts=temporal_facts,
        query="Fix context firewall",
        as_of="2026-02-01T00:00:00+00:00",
    )

    bundle = result["proof_bundle"]
    item = bundle["context_items"][0]

    assert result["format"] == "dhee_graph_proof_bundle.v1"
    assert bundle["schema_version"] == GRAPH_PROOF_BUNDLE_SCHEMA
    assert bundle["summary"]["context_item_count"] == 1
    assert bundle["summary"]["graph_attached_count"] == 1
    assert bundle["summary"]["temporal_fact_backed_count"] == 1
    assert item["context_id"] == "ctx_firewall"
    assert item["why_included"] == "localizer selected source file"
    assert item["completeness"]["has_graph_path"] is True
    assert item["sources"]["scenes"] == ["scene_firewall"]
    assert item["sources"]["event_ids"] == ["evt_firewall"]
    assert "dhee/context_firewall.py" in item["sources"]["files"]
    assert "tests/test_context_firewall.py" in item["sources"]["tests"]
    assert item["temporal_validity"]["basis"] == "temporal_facts"
    assert item["temporal_validity"]["active"] is True
    assert item["temporal_validity"]["fact_ids"] == ["tf_firewall"]
    assert item["graph_paths"][0]["status"] == "matched"
    assert "file:dhee/context_firewall.py" in item["graph_paths"][0]["node_ids"]
    assert any(path["relations"] == ["tested_by"] for path in item["graph_paths"])


def test_graph_proof_bundle_can_derive_context_items_from_context_graph_proof_items():
    result = build_graph_proof_bundle(
        context_items=None,
        context_graph=_context_graph(),
        temporal_facts=[
            {
                "id": "tf_test",
                "fact_text": "tests/test_context_firewall.py is the owned regression test.",
                "subject": "tests/test_context_firewall.py",
                "predicate": "role",
                "object": "owned regression test",
                "valid_from": "2026-01-01T00:00:00+00:00",
                "observed_at": "2026-01-01T00:00:00+00:00",
                "confidence": 0.75,
                "source_scene": "scene_test",
                "source_event_ids": ["evt_test"],
            }
        ],
        query="Fix context firewall",
        as_of="2026-01-15T00:00:00+00:00",
    )

    bundle = result["proof_bundle"]
    by_id = {item["context_id"]: item for item in bundle["context_items"]}

    assert set(by_id) == {"file:dhee/context_firewall.py", "file:tests/test_context_firewall.py"}
    assert by_id["file:dhee/context_firewall.py"]["why_included"] == "localized by goal token overlap; owned test exists"
    assert by_id["file:tests/test_context_firewall.py"]["sources"]["tests"] == ["tests/test_context_firewall.py"]
    assert by_id["file:tests/test_context_firewall.py"]["temporal_facts"][0]["id"] == "tf_test"
    assert bundle["summary"]["graph_attached_count"] == 2


def test_graph_proof_bundle_records_unmatched_context_item_without_dropping_it():
    result = build_graph_proof_bundle(
        [
            {
                "context_id": "ctx_note",
                "kind": "note",
                "title": "Architectural note without graph reference",
                "source_scene": "scene_note",
                "source_event_ids": ["evt_note"],
                "valid_from": "2026-03-01T00:00:00+00:00",
                "confidence": 0.6,
            }
        ],
        repo_graph={"nodes": [], "edges": []},
        context_graph={"nodes": [], "edges": []},
        as_of="2026-03-02T00:00:00+00:00",
    )

    bundle = result["proof_bundle"]
    item = bundle["context_items"][0]

    assert bundle["summary"]["context_item_count"] == 1
    assert bundle["summary"]["graph_attached_count"] == 0
    assert bundle["summary"]["missing_graph_path_context_ids"] == ["ctx_note"]
    assert item["graph_paths"][0]["status"] == "unmatched"
    assert item["sources"]["scenes"] == ["scene_note"]
    assert item["sources"]["event_ids"] == ["evt_note"]
    assert item["temporal_validity"]["basis"] == "context_item"
    assert item["temporal_validity"]["active"] is True
    assert "graph_path" in item["completeness"]["missing"]
