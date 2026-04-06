import json
import os
import time

import pytest

from dhee.core.belief import (
    BeliefFreshnessStatus,
    BeliefLifecycleStatus,
    BeliefNode,
    BeliefProtectionLevel,
    BeliefStatus,
    BeliefStore,
)


def test_belief_store_debugger_actions(tmp_path):
    store = BeliefStore(data_dir=str(tmp_path / "beliefs"))

    belief, _ = store.add_belief(
        user_id="u1",
        claim="Project uses PostgreSQL 16",
        domain="system_state",
        confidence=0.7,
        source="memory",
    )
    assert store.get_belief_history(belief.id)

    pinned = store.pin_belief(belief.id)
    assert pinned is not None
    assert pinned.protection_level == BeliefProtectionLevel.PINNED

    stale = store.mark_stale(belief.id, reason="Config changed")
    assert stale is not None
    assert stale.freshness_status == BeliefFreshnessStatus.STALE

    corrected = store.correct_belief(belief.id, "Project uses PostgreSQL 17", reason="Verified migration")
    assert corrected is not None
    old_belief, new_belief = corrected
    assert old_belief.freshness_status == BeliefFreshnessStatus.SUPERSEDED
    assert old_belief.successor_id == new_belief.id

    tombstoned = store.tombstone_belief(old_belief.id, reason="Superseded old belief")
    assert tombstoned is not None
    assert tombstoned.lifecycle_status == BeliefLifecycleStatus.TOMBSTONED

    merged_target, _ = store.add_belief(
        user_id="u1",
        claim="Primary database engine is PostgreSQL",
        domain="system_state",
        confidence=0.6,
        source="memory",
    )
    merged = store.merge_beliefs(merged_target.id, new_belief.id, reason="Deduplicate database belief")
    assert merged is not None
    assert merged.id == merged_target.id


def test_belief_store_migrates_legacy_json(tmp_path):
    beliefs_dir = tmp_path / "beliefs"
    beliefs_dir.mkdir(parents=True, exist_ok=True)

    legacy = BeliefNode(
        id="legacy-belief",
        user_id="user-1",
        claim="User prefers concise answers",
        domain="user_preference",
        status=BeliefStatus.HELD,
        confidence=0.8,
        created_at=time.time() - 100,
        updated_at=time.time() - 50,
        truth_status=BeliefStatus.HELD,
        origin="user",
    )
    legacy.add_evidence("User explicitly asked for shorter replies", True, source="user", confidence=0.9)

    with open(beliefs_dir / "legacy-belief.json", "w", encoding="utf-8") as handle:
        json.dump(legacy.to_dict(), handle)

    store = BeliefStore(data_dir=str(beliefs_dir))
    migrated = store.get_belief("legacy-belief")
    assert migrated is not None
    assert migrated.origin == "user"
    assert (beliefs_dir / "legacy_json_backup" / "legacy-belief.json").exists()
    assert store.get_belief_history("legacy-belief")


def test_belief_store_records_influence(tmp_path):
    store = BeliefStore(data_dir=str(tmp_path / "beliefs"))
    belief, _ = store.add_belief("u1", "Auth uses JWT rotation", "system_state", 0.7)

    store.record_influence(
        belief.id,
        user_id="u1",
        influence_type="included",
        query="auth bug",
        metadata={"surface": "context"},
    )

    history = store.get_belief_impact(belief.id)
    assert len(history) == 1
    assert history[0]["influence_type"] == "included"


def test_debugger_api_routes(tmp_path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from dhee.debugger_api import create_app

    root = tmp_path / "buddhi"
    store = BeliefStore(data_dir=str(root / "beliefs"))
    first, _ = store.add_belief("u1", "Repo uses FastAPI", "system_state", 0.8, source="user")
    second, _ = store.add_belief("u1", "Repo does not use FastAPI", "system_state", 0.6, source="user")
    store.record_influence(first.id, "u1", "included", query="api service")

    app = create_app(str(root))
    client = TestClient(app)

    overview = client.get("/api/debugger/overview")
    assert overview.status_code == 200
    assert overview.json()["counts"]["active"] >= 2

    beliefs = client.get("/api/debugger/beliefs", params={"user_id": "u1"})
    assert beliefs.status_code == 200
    assert beliefs.json()["total"] >= 2

    detail = client.get(f"/api/debugger/beliefs/{first.id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == first.id

    impact = client.get(f"/api/debugger/beliefs/{first.id}/impact")
    assert impact.status_code == 200
    assert impact.json()["items"][0]["influence_type"] == "included"

    contradictions = client.get("/api/debugger/contradictions", params={"user_id": "u1"})
    assert contradictions.status_code == 200
    assert contradictions.json()["items"]

    stale = client.post(f"/api/debugger/beliefs/{second.id}/mark-stale", json={"reason": "Reviewed"})
    assert stale.status_code == 200
    assert stale.json()["freshness_status"] == "stale"
