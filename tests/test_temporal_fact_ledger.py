from pathlib import Path

from dhee import mcp_slim
from dhee.temporal_fact_ledger import TemporalFactLedger


def test_temporal_fact_conflict_invalidates_without_deleting_and_supports_as_of(tmp_path):
    ledger = TemporalFactLedger(tmp_path / "facts.db")

    old = ledger.assert_fact(
        fact_text="User prefers VS Code for Python work.",
        user_id="u1",
        namespace="prefs",
        subject="user",
        predicate="preferred_editor",
        object="vscode",
        observed_at="2026-01-01T00:00:00+00:00",
        source_scene="scene_1",
        source_event_ids=["evt_1"],
        source_memory_ids=["mem_1"],
        evidence=[{"kind": "message", "ref": "evt_1"}],
        confidence=0.8,
    )
    old_id = old["fact"]["id"]

    new = ledger.assert_fact(
        fact_text="User now prefers Cursor for Python work.",
        user_id="u1",
        namespace="prefs",
        subject="user",
        predicate="preferred_editor",
        object="cursor",
        observed_at="2026-02-01T00:00:00+00:00",
        source_scene="scene_2",
        source_event_ids=["evt_2"],
        evidence=[{"kind": "message", "ref": "evt_2"}],
        confidence=0.9,
    )

    assert new["reused"] is False
    assert new["fact"]["active"] is True
    assert new["invalidated"][0]["id"] == old_id
    assert new["invalidated"][0]["valid_to"] == "2026-02-01T00:00:00+00:00"
    assert new["invalidated"][0]["contradicted_by"] == [new["fact"]["id"]]

    active_now = ledger.search("preferred editor", user_id="u1", namespace="prefs")
    assert [fact["object"] for fact in active_now["results"]] == ["cursor"]

    active_then = ledger.search(
        "preferred editor",
        user_id="u1",
        namespace="prefs",
        as_of="2026-01-15T00:00:00+00:00",
    )
    assert [fact["object"] for fact in active_then["results"]] == ["vscode"]

    old_with_events = ledger.get_fact(old_id, user_id="u1", include_events=True)
    assert old_with_events["status"] == "invalidated"
    assert [event["event_type"] for event in old_with_events["events"]] == ["ASSERT", "INVALIDATE"]
    assert old_with_events["source_scene"] == "scene_1"
    assert old_with_events["source_event_ids"] == ["evt_1"]

    stats = ledger.stats(user_id="u1", namespace="prefs")
    assert stats["total"] == 2
    assert stats["active"] == 1
    assert stats["by_status"]["invalidated"] == 1
    ledger.close()


def test_temporal_fact_reasserts_identical_active_fact_and_manual_invalidation(tmp_path):
    ledger = TemporalFactLedger(tmp_path / "facts.db")
    first = ledger.assert_fact(
        fact_text="Project uses pytest.",
        user_id="u1",
        namespace="repo",
        subject="project",
        predicate="test_runner",
        object="pytest",
        observed_at="2026-03-01T00:00:00+00:00",
    )
    second = ledger.assert_fact(
        fact_text="Project uses pytest.",
        user_id="u1",
        namespace="repo",
        subject="project",
        predicate="test_runner",
        object="pytest",
        observed_at="2026-03-02T00:00:00+00:00",
    )

    assert second["reused"] is True
    assert second["fact"]["id"] == first["fact"]["id"]
    events = ledger.get_fact(first["fact"]["id"], user_id="u1", include_events=True)["events"]
    assert [event["event_type"] for event in events] == ["ASSERT", "REASSERT"]

    invalidated = ledger.invalidate_fact(
        first["fact"]["id"],
        user_id="u1",
        reason="project migrated test runner",
        invalidated_at="2026-04-01T00:00:00+00:00",
    )
    assert invalidated["ok"] is True
    assert invalidated["fact"]["active"] is False
    assert invalidated["fact"]["invalidation_reason"] == "project migrated test runner"
    assert ledger.search("pytest", user_id="u1", namespace="repo")["results"] == []
    assert ledger.search("pytest", user_id="u1", namespace="repo", as_of="2026-03-15T00:00:00+00:00")["results"][0]["object"] == "pytest"
    ledger.close()


def test_temporal_fact_mcp_slim_handlers_use_scoped_db(tmp_path):
    db_path = str(tmp_path / "facts.db")
    asserted = mcp_slim.HANDLERS["dhee_temporal_fact_assert"](
        {
            "db_path": db_path,
            "user_id": "u1",
            "namespace": "prefs",
            "fact_text": "User prefers high quality code.",
            "subject": "user",
            "predicate": "quality_preference",
            "object": "high",
            "source_scene": "scene_quality",
            "source_event_ids": ["evt_quality"],
        }
    )
    fact_id = asserted["fact"]["id"]

    searched = mcp_slim.HANDLERS["dhee_temporal_fact_search"](
        {"db_path": db_path, "user_id": "u1", "namespace": "prefs", "query": "quality"}
    )
    fetched = mcp_slim.HANDLERS["dhee_temporal_fact_get"](
        {"db_path": db_path, "user_id": "u1", "fact_id": fact_id, "include_events": True}
    )
    invalidated = mcp_slim.HANDLERS["dhee_temporal_fact_invalidate"](
        {"db_path": db_path, "user_id": "u1", "fact_id": fact_id, "reason": "updated preference"}
    )

    assert Path(db_path).exists()
    assert searched["results"][0]["id"] == fact_id
    assert fetched["ok"] is True
    assert fetched["fact"]["events"][0]["event_type"] == "ASSERT"
    assert invalidated["ok"] is True
