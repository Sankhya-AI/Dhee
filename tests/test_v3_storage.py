"""Tests for Dhee v3 event-sourced storage layer.

Covers:
- RawEventStore: add, dedup, correct, delete, supersedes chain
- BeliefStore: CRUD, confidence updates, contradiction tracking
- PolicyStore: CRUD, outcome recording, status transitions
- AnchorStore: CRUD, field updates, filtering
- InsightStore: CRUD, outcome recording
- HeuristicStore: CRUD, outcome recording
- DerivedLineageStore: source/dependent queries, contribution weights
- CognitionStore: coordinator integration
"""

import os
import sqlite3
import tempfile

import pytest

from dhee.core.events import RawEventStore, RawMemoryEvent, EventStatus
from dhee.core.derived_store import (
    BeliefStore,
    PolicyStore,
    AnchorStore,
    InsightStore,
    HeuristicStore,
    DerivedLineageStore,
    CognitionStore,
)
from dhee.core.storage import initialize_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_v3.db")


@pytest.fixture
def event_store(db_path):
    store = RawEventStore(db_path=db_path)
    yield store
    store.close()


@pytest.fixture
def cognition_store(db_path):
    store = CognitionStore(db_path=db_path)
    yield store
    store.close()


@pytest.fixture
def shared_conn(db_path):
    """Shared connection + lock for derived store tests."""
    import threading
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    lock = threading.RLock()
    yield conn, lock
    conn.close()


# =========================================================================
# RawEventStore Tests
# =========================================================================

class TestRawEventStore:

    def test_add_basic(self, event_store):
        event = event_store.add(content="User prefers dark mode", user_id="u1")
        assert event.event_id
        assert event.content == "User prefers dark mode"
        assert event.user_id == "u1"
        assert event.status == EventStatus.ACTIVE
        assert event.content_hash == RawMemoryEvent.compute_hash("User prefers dark mode")

    def test_add_with_metadata(self, event_store):
        event = event_store.add(
            content="test",
            user_id="u1",
            session_id="s1",
            source="mcp",
            metadata={"key": "value"},
        )
        assert event.session_id == "s1"
        assert event.source == "mcp"
        assert event.metadata == {"key": "value"}

    def test_dedup_same_content(self, event_store):
        e1 = event_store.add(content="same fact", user_id="u1")
        e2 = event_store.add(content="same fact", user_id="u1")
        assert e1.event_id == e2.event_id  # dedup returns existing

    def test_dedup_different_users(self, event_store):
        e1 = event_store.add(content="shared fact", user_id="u1")
        e2 = event_store.add(content="shared fact", user_id="u2")
        assert e1.event_id != e2.event_id  # different users = no dedup

    def test_get(self, event_store):
        e = event_store.add(content="test get", user_id="u1")
        fetched = event_store.get(e.event_id)
        assert fetched is not None
        assert fetched.content == "test get"

    def test_get_nonexistent(self, event_store):
        assert event_store.get("nonexistent") is None

    def test_get_by_hash(self, event_store):
        e = event_store.add(content="hash lookup", user_id="u1")
        found = event_store.get_by_hash(e.content_hash, "u1")
        assert found is not None
        assert found.event_id == e.event_id

    def test_correct(self, event_store):
        original = event_store.add(content="I live in Ghazipur", user_id="u1")
        correction = event_store.correct(
            original.event_id, "I live in Bengaluru"
        )

        assert correction.supersedes_event_id == original.event_id
        assert correction.content == "I live in Bengaluru"
        assert correction.status == EventStatus.ACTIVE

        # Original should now be 'corrected'
        old = event_store.get(original.event_id)
        assert old.status == EventStatus.CORRECTED

    def test_correct_nonexistent(self, event_store):
        with pytest.raises(ValueError, match="not found"):
            event_store.correct("nonexistent", "new content")

    def test_correct_already_corrected(self, event_store):
        e = event_store.add(content="old", user_id="u1")
        event_store.correct(e.event_id, "new")
        with pytest.raises(ValueError, match="Cannot correct"):
            event_store.correct(e.event_id, "newer")

    def test_delete(self, event_store):
        e = event_store.add(content="to delete", user_id="u1")
        assert event_store.delete(e.event_id) is True
        deleted = event_store.get(e.event_id)
        assert deleted.status == EventStatus.DELETED

    def test_delete_idempotent(self, event_store):
        e = event_store.add(content="to delete", user_id="u1")
        assert event_store.delete(e.event_id) is True
        assert event_store.delete(e.event_id) is False  # already deleted

    def test_delete_nonexistent(self, event_store):
        with pytest.raises(ValueError, match="not found"):
            event_store.delete("nonexistent")

    def test_list_by_user(self, event_store):
        event_store.add(content="fact1", user_id="u1")
        event_store.add(content="fact2", user_id="u1")
        event_store.add(content="fact3", user_id="u2")

        u1_events = event_store.list_by_user("u1")
        assert len(u1_events) == 2

        u2_events = event_store.list_by_user("u2")
        assert len(u2_events) == 1

    def test_list_by_user_with_status(self, event_store):
        e1 = event_store.add(content="active", user_id="u1")
        e2 = event_store.add(content="will delete", user_id="u1")
        event_store.delete(e2.event_id)

        active = event_store.list_by_user("u1", status=EventStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].content == "active"

    def test_supersedes_chain(self, event_store):
        e1 = event_store.add(content="v1", user_id="u1")
        e2 = event_store.correct(e1.event_id, "v2")
        # Can't correct e1 again (it's already corrected), but we can
        # correct e2 to get a 3-step chain
        e3 = event_store.correct(e2.event_id, "v3")

        chain = event_store.get_supersedes_chain(e3.event_id)
        assert len(chain) == 3
        assert chain[0].content == "v3"
        assert chain[1].content == "v2"
        assert chain[2].content == "v1"

    def test_count(self, event_store):
        event_store.add(content="a", user_id="u1")
        event_store.add(content="b", user_id="u1")
        event_store.add(content="c", user_id="u1")

        assert event_store.count("u1") == 3
        assert event_store.count("u1", status=EventStatus.ACTIVE) == 3
        assert event_store.count("u2") == 0

    def test_dedup_after_delete(self, event_store):
        """Deleted content should not block new addition of same content."""
        e1 = event_store.add(content="ephemeral", user_id="u1")
        event_store.delete(e1.event_id)
        # Adding same content again should create new event (old is deleted, not active)
        e2 = event_store.add(content="ephemeral", user_id="u1")
        assert e2.event_id != e1.event_id


# =========================================================================
# BeliefStore Tests
# =========================================================================

class TestBeliefStore:

    def test_add_and_get(self, shared_conn):
        conn, lock = shared_conn
        store = BeliefStore(conn, lock)

        bid = store.add(
            user_id="u1",
            claim="Python is dynamically typed",
            domain="programming",
            confidence=0.8,
        )
        belief = store.get(bid)
        assert belief is not None
        assert belief["claim"] == "Python is dynamically typed"
        assert belief["domain"] == "programming"
        assert belief["confidence"] == 0.8
        assert belief["status"] == "proposed"

    def test_update_confidence_auto_status(self, shared_conn):
        conn, lock = shared_conn
        store = BeliefStore(conn, lock)

        bid = store.add(user_id="u1", claim="test", confidence=0.5)

        # High confidence → held
        store.update_confidence(bid, 0.9)
        b = store.get(bid)
        assert b["status"] == "held"
        assert b["confidence"] == 0.9
        assert len(b["revisions"]) == 1

        # Low confidence → retracted
        store.update_confidence(bid, 0.05)
        b = store.get(bid)
        assert b["status"] == "retracted"

    def test_update_confidence_with_evidence(self, shared_conn):
        conn, lock = shared_conn
        store = BeliefStore(conn, lock)

        bid = store.add(user_id="u1", claim="test", confidence=0.5)
        store.update_confidence(
            bid, 0.7,
            evidence={"content": "saw it in docs", "supports": True},
            revision_reason="documentation found",
        )

        b = store.get(bid)
        assert len(b["evidence"]) == 1
        assert b["evidence"][0]["content"] == "saw it in docs"

    def test_contradiction(self, shared_conn):
        conn, lock = shared_conn
        store = BeliefStore(conn, lock)

        b1 = store.add(user_id="u1", claim="Earth is round", confidence=0.9)
        b2 = store.add(user_id="u1", claim="Earth is flat", confidence=0.3)

        store.add_contradiction(b1, b2)

        belief1 = store.get(b1)
        belief2 = store.get(b2)
        assert b2 in belief1["contradicts_ids"]
        assert b1 in belief2["contradicts_ids"]
        assert belief1["status"] == "challenged"
        assert belief2["status"] == "challenged"

    def test_list_by_user(self, shared_conn):
        conn, lock = shared_conn
        store = BeliefStore(conn, lock)

        store.add(user_id="u1", claim="a", domain="sci", confidence=0.9)
        store.add(user_id="u1", claim="b", domain="sci", confidence=0.3)
        store.add(user_id="u1", claim="c", domain="eng", confidence=0.7)

        sci = store.list_by_user("u1", domain="sci")
        assert len(sci) == 2

        high = store.list_by_user("u1", min_confidence=0.5)
        assert len(high) == 2

    def test_set_status(self, shared_conn):
        conn, lock = shared_conn
        store = BeliefStore(conn, lock)

        bid = store.add(user_id="u1", claim="test")
        assert store.set_status(bid, "stale") is True
        assert store.get(bid)["status"] == "stale"

    def test_get_by_invalidation_status(self, shared_conn):
        conn, lock = shared_conn
        store = BeliefStore(conn, lock)

        b1 = store.add(user_id="u1", claim="stale one")
        store.set_status(b1, "stale")
        b2 = store.add(user_id="u1", claim="active one")

        stale = store.get_by_invalidation_status("stale")
        assert len(stale) == 1
        assert stale[0]["belief_id"] == b1


# =========================================================================
# PolicyStore Tests
# =========================================================================

class TestPolicyStore:

    def test_add_and_get(self, shared_conn):
        conn, lock = shared_conn
        store = PolicyStore(conn, lock)

        pid = store.add(
            user_id="u1",
            name="Use git blame first",
            condition={"task_types": ["bug_fix"]},
            action={"approach": "Run git blame on failing file"},
            granularity="task",
        )
        policy = store.get(pid)
        assert policy is not None
        assert policy["name"] == "Use git blame first"
        assert policy["granularity"] == "task"
        assert policy["status"] == "proposed"
        assert policy["condition"]["task_types"] == ["bug_fix"]

    def test_record_outcome_success(self, shared_conn):
        conn, lock = shared_conn
        store = PolicyStore(conn, lock)

        pid = store.add(
            user_id="u1", name="test",
            condition={}, action={},
        )

        for _ in range(5):
            store.record_outcome(pid, success=True, baseline_score=0.5, actual_score=0.8)

        p = store.get(pid)
        assert p["apply_count"] == 5
        assert p["success_count"] == 5
        assert p["failure_count"] == 0
        assert p["status"] == "validated"  # win_rate >= 0.6 after 5+
        assert p["utility"] > 0

    def test_record_outcome_deprecated(self, shared_conn):
        conn, lock = shared_conn
        store = PolicyStore(conn, lock)

        pid = store.add(
            user_id="u1", name="bad policy",
            condition={}, action={},
        )

        for _ in range(6):
            store.record_outcome(pid, success=False)

        p = store.get(pid)
        assert p["status"] == "deprecated"

    def test_record_outcome_utility_ema(self, shared_conn):
        conn, lock = shared_conn
        store = PolicyStore(conn, lock)

        pid = store.add(
            user_id="u1", name="ema test",
            condition={}, action={},
        )

        store.record_outcome(pid, success=True, baseline_score=0.5, actual_score=0.9)
        p = store.get(pid)
        # First delta = 0.4, utility = 0.3 * 0.4 + 0.7 * 0.0 = 0.12
        assert abs(p["utility"] - 0.12) < 0.01

    def test_list_by_user(self, shared_conn):
        conn, lock = shared_conn
        store = PolicyStore(conn, lock)

        store.add(user_id="u1", name="p1", condition={}, action={}, granularity="task")
        store.add(user_id="u1", name="p2", condition={}, action={}, granularity="step")

        task_policies = store.list_by_user("u1", granularity="task")
        assert len(task_policies) == 1


# =========================================================================
# AnchorStore Tests
# =========================================================================

class TestAnchorStore:

    def test_add_and_get(self, shared_conn):
        conn, lock = shared_conn
        store = AnchorStore(conn, lock)

        aid = store.add(
            user_id="u1",
            era="bengaluru_work",
            place="Bengaluru",
            place_type="city",
            activity="coding",
        )
        anchor = store.get(aid)
        assert anchor is not None
        assert anchor["era"] == "bengaluru_work"
        assert anchor["place"] == "Bengaluru"
        assert anchor["activity"] == "coding"

    def test_get_by_event(self, shared_conn):
        conn, lock = shared_conn
        store = AnchorStore(conn, lock)

        aid = store.add(user_id="u1", memory_event_id="evt-123")
        found = store.get_by_event("evt-123")
        assert found is not None
        assert found["anchor_id"] == aid

    def test_list_filtered(self, shared_conn):
        conn, lock = shared_conn
        store = AnchorStore(conn, lock)

        store.add(user_id="u1", era="school", place="Ghazipur")
        store.add(user_id="u1", era="work", place="Bengaluru")
        store.add(user_id="u1", era="school", place="Delhi")

        school = store.list_by_user("u1", era="school")
        assert len(school) == 2

        blr = store.list_by_user("u1", place="Bengaluru")
        assert len(blr) == 1

    def test_update_fields(self, shared_conn):
        conn, lock = shared_conn
        store = AnchorStore(conn, lock)

        aid = store.add(user_id="u1", era="old_era")
        assert store.update_fields(aid, era="new_era") is True
        assert store.get(aid)["era"] == "new_era"

    def test_update_fields_rejects_invalid(self, shared_conn):
        conn, lock = shared_conn
        store = AnchorStore(conn, lock)

        aid = store.add(user_id="u1")
        # user_id is not in the allowed update set
        assert store.update_fields(aid, user_id="hacker") is False


# =========================================================================
# InsightStore Tests
# =========================================================================

class TestInsightStore:

    def test_add_and_get(self, shared_conn):
        conn, lock = shared_conn
        store = InsightStore(conn, lock)

        iid = store.add(
            user_id="u1",
            content="Strict criteria + balanced scoring works best",
            insight_type="strategy",
            confidence=0.7,
        )
        insight = store.get(iid)
        assert insight["content"] == "Strict criteria + balanced scoring works best"
        assert insight["insight_type"] == "strategy"

    def test_record_outcome_success(self, shared_conn):
        conn, lock = shared_conn
        store = InsightStore(conn, lock)

        iid = store.add(user_id="u1", content="test", confidence=0.5)
        store.record_outcome(iid, success=True)
        i = store.get(iid)
        assert i["validation_count"] == 1
        assert i["confidence"] == 0.55  # 0.5 + 0.05

    def test_record_outcome_failure(self, shared_conn):
        conn, lock = shared_conn
        store = InsightStore(conn, lock)

        iid = store.add(user_id="u1", content="test", confidence=0.5)
        store.record_outcome(iid, success=False)
        i = store.get(iid)
        assert i["invalidation_count"] == 1
        assert i["confidence"] == 0.4  # 0.5 - 0.1

    def test_record_outcome_with_scores(self, shared_conn):
        conn, lock = shared_conn
        store = InsightStore(conn, lock)

        iid = store.add(user_id="u1", content="test", confidence=0.5)
        store.record_outcome(iid, success=True, baseline_score=0.5, actual_score=0.8)
        i = store.get(iid)
        # utility = 0.3 * 0.3 + 0.7 * 0.0 = 0.09
        assert abs(i["utility"] - 0.09) < 0.01

    def test_list_by_type(self, shared_conn):
        conn, lock = shared_conn
        store = InsightStore(conn, lock)

        store.add(user_id="u1", content="a", insight_type="warning")
        store.add(user_id="u1", content="b", insight_type="strategy")
        store.add(user_id="u1", content="c", insight_type="warning")

        warnings = store.list_by_user("u1", insight_type="warning")
        assert len(warnings) == 2


# =========================================================================
# HeuristicStore Tests
# =========================================================================

class TestHeuristicStore:

    def test_add_and_get(self, shared_conn):
        conn, lock = shared_conn
        store = HeuristicStore(conn, lock)

        hid = store.add(
            user_id="u1",
            content="Start with the most constrained component",
            abstraction_level="universal",
        )
        h = store.get(hid)
        assert h["content"] == "Start with the most constrained component"
        assert h["abstraction_level"] == "universal"

    def test_record_outcome(self, shared_conn):
        conn, lock = shared_conn
        store = HeuristicStore(conn, lock)

        hid = store.add(user_id="u1", content="test", confidence=0.5)
        store.record_outcome(hid, success=True, baseline_score=0.4, actual_score=0.7)

        h = store.get(hid)
        assert h["validation_count"] == 1
        assert h["confidence"] == 0.55
        assert abs(h["utility"] - 0.09) < 0.01  # 0.3 * 0.3 + 0.7 * 0

    def test_list_by_level(self, shared_conn):
        conn, lock = shared_conn
        store = HeuristicStore(conn, lock)

        store.add(user_id="u1", content="a", abstraction_level="specific")
        store.add(user_id="u1", content="b", abstraction_level="domain")
        store.add(user_id="u1", content="c", abstraction_level="universal")

        universal = store.list_by_user("u1", abstraction_level="universal")
        assert len(universal) == 1


# =========================================================================
# DerivedLineageStore Tests
# =========================================================================

class TestDerivedLineageStore:

    def test_add_and_get_sources(self, shared_conn):
        conn, lock = shared_conn
        store = DerivedLineageStore(conn, lock)

        store.add("belief", "b1", "evt1", contribution_weight=0.6)
        store.add("belief", "b1", "evt2", contribution_weight=0.4)

        sources = store.get_sources("belief", "b1")
        assert len(sources) == 2
        weights = {s["source_event_id"]: s["contribution_weight"] for s in sources}
        assert weights["evt1"] == 0.6
        assert weights["evt2"] == 0.4

    def test_get_dependents(self, shared_conn):
        conn, lock = shared_conn
        store = DerivedLineageStore(conn, lock)

        store.add("belief", "b1", "evt1")
        store.add("policy", "p1", "evt1")
        store.add("belief", "b2", "evt2")

        deps = store.get_dependents("evt1")
        assert len(deps) == 2
        types = {d["derived_type"] for d in deps}
        assert types == {"belief", "policy"}

    def test_add_batch(self, shared_conn):
        conn, lock = shared_conn
        store = DerivedLineageStore(conn, lock)

        ids = store.add_batch(
            "insight", "i1",
            ["evt1", "evt2", "evt3"],
            weights=[0.5, 0.3, 0.2],
        )
        assert len(ids) == 3
        assert store.get_source_count("insight", "i1") == 3

    def test_contribution_weight(self, shared_conn):
        conn, lock = shared_conn
        store = DerivedLineageStore(conn, lock)

        store.add("belief", "b1", "evt1", contribution_weight=0.7)
        w = store.get_contribution_weight("belief", "b1", "evt1")
        assert w == 0.7

        # Nonexistent
        assert store.get_contribution_weight("belief", "b1", "evt999") is None

    def test_delete_for_derived(self, shared_conn):
        conn, lock = shared_conn
        store = DerivedLineageStore(conn, lock)

        store.add("belief", "b1", "evt1")
        store.add("belief", "b1", "evt2")
        assert store.get_source_count("belief", "b1") == 2

        deleted = store.delete_for_derived("belief", "b1")
        assert deleted == 2
        assert store.get_source_count("belief", "b1") == 0


# =========================================================================
# CognitionStore Integration Tests
# =========================================================================

class TestCognitionStore:

    def test_full_lifecycle(self, cognition_store):
        """End-to-end: raw event → derived belief → lineage → invalidation status."""
        cs = cognition_store

        # 1. Store raw event
        event = cs.events.add(content="User prefers dark mode", user_id="u1")
        assert event.status == EventStatus.ACTIVE

        # 2. Derive a belief from it
        bid = cs.beliefs.add(
            user_id="u1",
            claim="User prefers dark mode",
            domain="preferences",
            confidence=0.8,
            source_memory_ids=[event.event_id],
        )

        # 3. Record lineage
        cs.lineage.add("belief", bid, event.event_id, contribution_weight=1.0)

        # 4. Verify lineage
        sources = cs.lineage.get_sources("belief", bid)
        assert len(sources) == 1
        assert sources[0]["source_event_id"] == event.event_id

        deps = cs.lineage.get_dependents(event.event_id)
        assert len(deps) == 1
        assert deps[0]["derived_id"] == bid

        # 5. Correct the raw event
        correction = cs.events.correct(
            event.event_id, "User prefers light mode"
        )
        assert correction.supersedes_event_id == event.event_id

        # 6. Mark belief as stale (soft invalidation would do this)
        cs.beliefs.set_status(bid, "stale")
        stale = cs.beliefs.get_by_invalidation_status("stale")
        assert len(stale) == 1

    def test_policy_lifecycle(self, cognition_store):
        cs = cognition_store

        # Create event + policy + lineage
        e = cs.events.add(content="git blame helped find bug", user_id="u1")
        pid = cs.policies.add(
            user_id="u1",
            name="git blame first",
            condition={"task_types": ["bug_fix"]},
            action={"approach": "Run git blame"},
        )
        cs.lineage.add("policy", pid, e.event_id)

        # Record outcomes → validated
        for _ in range(6):
            cs.policies.record_outcome(pid, success=True, baseline_score=0.4, actual_score=0.8)

        p = cs.policies.get(pid)
        assert p["status"] == "validated"
        assert p["utility"] > 0

    def test_multi_type_lineage(self, cognition_store):
        """One raw event feeds multiple derived types."""
        cs = cognition_store

        e = cs.events.add(content="auth tokens expire in prod", user_id="u1")

        bid = cs.beliefs.add(user_id="u1", claim="Tokens expire in prod")
        pid = cs.policies.add(
            user_id="u1", name="check token expiry",
            condition={}, action={},
        )
        iid = cs.insights.add(user_id="u1", content="Token expiry causes outages")

        cs.lineage.add("belief", bid, e.event_id)
        cs.lineage.add("policy", pid, e.event_id)
        cs.lineage.add("insight", iid, e.event_id)

        deps = cs.lineage.get_dependents(e.event_id)
        assert len(deps) == 3
        types = {d["derived_type"] for d in deps}
        assert types == {"belief", "policy", "insight"}

    def test_shared_connection(self, cognition_store):
        """All stores share the same connection — verify cross-store visibility."""
        cs = cognition_store

        # Write through events store
        e = cs.events.add(content="test visibility", user_id="u1")

        # Write through beliefs store
        bid = cs.beliefs.add(user_id="u1", claim="test")

        # Lineage can see both (same connection)
        cs.lineage.add("belief", bid, e.event_id)
        sources = cs.lineage.get_sources("belief", bid)
        assert len(sources) == 1
