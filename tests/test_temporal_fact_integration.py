from dhee.temporal_fact_integration import (
    active_fact_context_cards,
    collect_candidate_facts,
    extract_candidate_facts_from_checkpoint,
    promote_temporal_facts,
)
from dhee.temporal_fact_ledger import TemporalFactLedger


def test_scene_card_promotion_asserts_structured_facts_with_scene_provenance(tmp_path):
    ledger = TemporalFactLedger(tmp_path / "facts.db")
    scene_card = {
        "id": "scene_pref",
        "title": "Preference capture",
        "summary": "The user prefers Cursor for Python work.",
        "created_at": "2026-05-01T10:00:00+00:00",
        "confidence": 0.86,
        "privacy_scope": "personal",
        "provenance": {"source_event_ids": ["evt_scene"], "source_memory_ids": ["mem_scene_1"]},
        "evidence_refs": [{"kind": "memory", "ref": "mem_scene_2"}],
        "temporal_facts": [
            {
                "fact_text": "User prefers Cursor for Python work.",
                "subject": "user",
                "predicate": "preferred_editor",
                "object": "cursor",
                "confidence": 0.92,
            }
        ],
    }

    result = promote_temporal_facts(
        ledger=ledger,
        scene_cards=[scene_card],
        user_id="u1",
        namespace="prefs",
        query="preferred editor",
    )

    assert result["candidate_count"] == 2
    structured = [item for item in result["assertions"] if item["fact"]["predicate"] == "preferred_editor"][0]
    fact = structured["fact"]
    assert fact["source_scene"] == "scene_pref"
    assert fact["source_event_ids"] == ["evt_scene"]
    assert fact["source_memory_ids"] == ["mem_scene_1", "mem_scene_2"]
    assert fact["evidence"][0]["kind"] == "scene_card"
    assert fact["metadata"]["origin"] == "scene_card"

    cards = result["active_fact_context_cards"]
    assert cards
    assert cards[0]["format"] == "dhee.temporal_fact_context_card.v1"
    assert cards[0]["source_scene"] == "scene_pref"
    assert "mem_scene_1" in cards[0]["provenance"]["source_memory_ids"]
    ledger.close()


def test_checkpoint_summary_extraction_promotes_conflicting_active_fact(tmp_path):
    ledger = TemporalFactLedger(tmp_path / "facts.db")
    first = {
        "checkpoint_id": "cp_1",
        "summary": "Project uses pytest as its test runner.",
        "created_at": "2026-05-02T09:00:00+00:00",
        "source_event_id": "evt_cp_1",
        "memory_ids": ["mem_cp_1"],
    }
    second = {
        "checkpoint_id": "cp_2",
        "summary": "Project uses unittest as its test runner.",
        "created_at": "2026-05-03T09:00:00+00:00",
        "source_event_id": "evt_cp_2",
        "memory_ids": ["mem_cp_2"],
    }

    candidates = extract_candidate_facts_from_checkpoint(first, user_id="u1", namespace="repo")
    assert [(candidate.subject, candidate.predicate, candidate.object) for candidate in candidates] == [
        ("Project", "uses", "pytest as its test runner")
    ]

    promote_temporal_facts(ledger=ledger, checkpoints=[first], user_id="u1", namespace="repo")
    promoted = promote_temporal_facts(
        ledger=ledger,
        checkpoints=[second],
        user_id="u1",
        namespace="repo",
        query="test runner",
    )

    assert promoted["invalidated_count"] == 1
    assert [card["object"] for card in promoted["active_fact_context_cards"]] == ["unittest as its test runner"]

    historical = active_fact_context_cards(
        ledger=ledger,
        query="test runner",
        user_id="u1",
        namespace="repo",
        as_of="2026-05-02T12:00:00+00:00",
    )
    assert [card["object"] for card in historical] == ["pytest as its test runner"]
    ledger.close()


def test_memory_rows_promote_metadata_facts_and_dedupe_candidates(tmp_path):
    ledger = TemporalFactLedger(tmp_path / "facts.db")
    memory_row = {
        "id": "mem_1",
        "user_id": "u1",
        "namespace": "repo",
        "memory": "Dhee stores temporal facts in SQLite.",
        "source_event_id": "evt_mem_1",
        "scene_id": "scene_storage",
        "created_at": "2026-05-04T08:00:00+00:00",
        "metadata": {
            "temporal_facts": [
                {
                    "fact_text": "Dhee stores temporal facts in SQLite.",
                    "subject": "Dhee",
                    "predicate": "stores",
                    "object": "temporal facts in SQLite",
                }
            ]
        },
    }

    candidates = collect_candidate_facts(memory_rows=[memory_row], user_id="u1", namespace="repo")
    assert len(candidates) == 1
    assert candidates[0].source_scene == "scene_storage"
    assert candidates[0].source_event_ids == ["evt_mem_1"]
    assert candidates[0].source_memory_ids == ["mem_1"]

    result = promote_temporal_facts(
        ledger=ledger,
        memory_rows=[memory_row],
        user_id="u1",
        namespace="repo",
        query="temporal facts sqlite",
    )

    assert result["asserted_count"] == 1
    card = result["active_fact_context_cards"][0]
    assert card["subject"] == "Dhee"
    assert card["predicate"] == "stores"
    assert card["source_scene"] == "scene_storage"
    assert card["provenance"]["source_event_ids"] == ["evt_mem_1"]
    ledger.close()

