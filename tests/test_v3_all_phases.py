"""Tests for Dhee v3 Phases 2-10.

Phase 2: Anchor resolver (per-field candidates, re-anchoring)
Phase 4: Distillation + Promotion pipeline
Phase 5: Lease manager + Job registry
Phase 7: RRF Fusion (5-stage pipeline)
Phase 8: Three-tier invalidation + Conflicts
Phase 6: Read model + delta overlay
Phase 9: Observability (v3_health)
Phase 10: Migration bridge (dual-write, backfill)
Phase 3: Sparse to_dict on UniversalEngram
"""

import json
import os
import sqlite3
import threading
import time

import pytest

from dhee.core.storage import initialize_schema
from dhee.core.events import RawEventStore, EventStatus
from dhee.core.derived_store import (
    BeliefStore, PolicyStore, AnchorStore, InsightStore,
    HeuristicStore, DerivedLineageStore, CognitionStore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_v3_phases.db")


@pytest.fixture
def store(db_path):
    s = CognitionStore(db_path=db_path)
    yield s
    s.close()


@pytest.fixture
def conn_lock(db_path):
    """Shared connection + lock for lower-level store tests."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    lock = threading.RLock()
    yield conn, lock
    conn.close()


# =========================================================================
# Phase 2: Anchor Resolver
# =========================================================================

class TestAnchorResolver:

    def test_submit_and_resolve(self, store):
        from dhee.core.anchor_resolver import AnchorCandidateStore, AnchorResolver

        anchor_id = store.anchors.add(user_id="u1")
        cand_store = AnchorCandidateStore(store.events._conn, store.events._lock)
        resolver = AnchorResolver(cand_store, store.anchors)

        # Submit competing candidates for 'place'
        cand_store.submit(anchor_id, "place", "Ghazipur", confidence=0.6)
        cand_store.submit(anchor_id, "place", "Bengaluru", confidence=0.9)

        result = resolver.resolve(anchor_id)
        assert result["resolved_fields"]["place"] == "Bengaluru"
        assert result["details"]["place"]["confidence"] == 0.9

        # Verify anchor was updated
        anchor = store.anchors.get(anchor_id)
        assert anchor["place"] == "Bengaluru"

    def test_re_anchor_correction(self, store):
        from dhee.core.anchor_resolver import AnchorCandidateStore, AnchorResolver

        anchor_id = store.anchors.add(user_id="u1", place="Ghazipur")
        cand_store = AnchorCandidateStore(store.events._conn, store.events._lock)
        resolver = AnchorResolver(cand_store, store.anchors)

        # Initial candidate
        cand_store.submit(anchor_id, "place", "Ghazipur", confidence=0.6)
        resolver.resolve(anchor_id)
        assert store.anchors.get(anchor_id)["place"] == "Ghazipur"

        # User corrects
        result = resolver.re_anchor(
            anchor_id, "place", "Delhi", confidence=0.95
        )
        assert result["resolved_fields"]["place"] == "Delhi"
        assert store.anchors.get(anchor_id)["place"] == "Delhi"

    def test_extract_and_submit(self, store):
        from dhee.core.anchor_resolver import AnchorCandidateStore, AnchorResolver

        anchor_id = store.anchors.add(user_id="u1")
        cand_store = AnchorCandidateStore(store.events._conn, store.events._lock)
        resolver = AnchorResolver(cand_store, store.anchors)

        cids = resolver.extract_and_submit(
            anchor_id, "I was coding at the office today"
        )
        assert len(cids) >= 1  # should detect 'coding' activity and 'office' place_type

        candidates = cand_store.get_candidates(anchor_id)
        field_names = {c["field_name"] for c in candidates}
        assert "activity" in field_names

    def test_invalid_field_rejected(self, store):
        from dhee.core.anchor_resolver import AnchorCandidateStore

        cand_store = AnchorCandidateStore(store.events._conn, store.events._lock)
        with pytest.raises(ValueError, match="Invalid anchor field"):
            cand_store.submit("a1", "invalid_field", "value")


# =========================================================================
# Phase 4: Distillation + Promotion
# =========================================================================

class TestDistillationPromotion:

    def test_submit_and_promote_belief(self, store):
        from dhee.core.distillation import (
            DistillationStore, DistillationCandidate, distill_belief_from_events,
        )
        from dhee.core.promotion import PromotionEngine

        conn = store.events._conn
        lock = store.events._lock

        # Create source events
        e1 = store.events.add(content="Python uses GIL", user_id="u1")
        e2 = store.events.add(content="Python GIL limits threading", user_id="u1")

        # Distill a belief candidate
        candidate = distill_belief_from_events(
            [e1.to_dict(), e2.to_dict()],
            user_id="u1", domain="programming",
        )
        assert candidate is not None

        # Submit to distillation store
        dist_store = DistillationStore(conn, lock)
        cid = dist_store.submit(candidate)
        assert cid is not None

        # Promote
        engine = PromotionEngine(
            distillation=dist_store,
            beliefs=store.beliefs,
            policies=store.policies,
            insights=store.insights,
            heuristics=store.heuristics,
            lineage=store.lineage,
        )
        result = engine.promote_pending(target_type="belief")
        assert result.promoted  # at least one promoted

        # Verify lineage was created
        promoted_id = result.promoted[0]
        sources = store.lineage.get_sources("belief", promoted_id)
        assert len(sources) >= 1

    def test_idempotent_dedup(self, store):
        from dhee.core.distillation import DistillationStore, DistillationCandidate

        conn = store.events._conn
        lock = store.events._lock
        dist_store = DistillationStore(conn, lock)

        candidate = DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1", "e2"],
            target_type="belief",
            canonical_key="test_dedup",
            payload={"user_id": "u1", "claim": "test"},
        )

        cid1 = dist_store.submit(candidate)
        assert cid1 == "c1"

        # Same idempotency key — should be deduped
        candidate2 = DistillationCandidate(
            candidate_id="c2",
            source_event_ids=["e1", "e2"],
            target_type="belief",
            canonical_key="test_dedup",
            payload={"user_id": "u1", "claim": "test"},
        )
        cid2 = dist_store.submit(candidate2)
        assert cid2 is None  # deduped

    def test_low_confidence_rejected(self, store):
        from dhee.core.distillation import DistillationStore, DistillationCandidate
        from dhee.core.promotion import PromotionEngine

        conn = store.events._conn
        lock = store.events._lock
        dist_store = DistillationStore(conn, lock)

        candidate = DistillationCandidate(
            candidate_id="c-low",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="low_conf",
            confidence=0.1,  # below MIN_PROMOTION_CONFIDENCE (0.3)
            payload={"user_id": "u1", "claim": "uncertain thing"},
        )
        dist_store.submit(candidate)

        engine = PromotionEngine(
            distillation=dist_store,
            beliefs=store.beliefs,
            policies=store.policies,
            insights=store.insights,
            heuristics=store.heuristics,
            lineage=store.lineage,
        )
        result = engine.promote_pending()
        assert "c-low" in result.rejected


# =========================================================================
# Phase 5: Lease Manager + Job Registry
# =========================================================================

class TestLeaseManager:

    def test_acquire_release(self, conn_lock):
        from dhee.core.lease_manager import LeaseManager

        conn, lock = conn_lock
        lm = LeaseManager(conn, lock)

        assert lm.acquire("job-1", "worker-a") is True
        assert lm.is_held("job-1") is True
        assert lm.get_holder("job-1") == "worker-a"

        # Different worker can't acquire
        assert lm.acquire("job-1", "worker-b") is False

        # Release
        assert lm.release("job-1", "worker-a") is True
        assert lm.is_held("job-1") is False

    def test_same_owner_renew(self, conn_lock):
        from dhee.core.lease_manager import LeaseManager

        conn, lock = conn_lock
        lm = LeaseManager(conn, lock)

        lm.acquire("job-1", "worker-a")
        assert lm.renew("job-1", "worker-a") is True

    def test_wrong_owner_cant_release(self, conn_lock):
        from dhee.core.lease_manager import LeaseManager

        conn, lock = conn_lock
        lm = LeaseManager(conn, lock)

        lm.acquire("job-1", "worker-a")
        assert lm.release("job-1", "worker-b") is False


class TestJobRegistry:

    def test_register_and_run(self, conn_lock):
        from dhee.core.lease_manager import LeaseManager
        from dhee.core.jobs import JobRegistry, Job

        conn, lock = conn_lock
        lm = LeaseManager(conn, lock)
        registry = JobRegistry(conn, lock, lm)

        class TestJob(Job):
            name = "test_job"
            def execute(self, payload):
                return {"sum": payload.get("a", 0) + payload.get("b", 0)}

        registry.register(TestJob)
        result = registry.run("test_job", payload={"a": 3, "b": 7})
        assert result["status"] == "completed"
        assert result["result"]["sum"] == 10

    def test_run_unknown_job(self, conn_lock):
        from dhee.core.lease_manager import LeaseManager
        from dhee.core.jobs import JobRegistry

        conn, lock = conn_lock
        lm = LeaseManager(conn, lock)
        registry = JobRegistry(conn, lock, lm)

        result = registry.run("nonexistent")
        assert result["status"] == "error"

    def test_health_check(self, conn_lock):
        from dhee.core.lease_manager import LeaseManager
        from dhee.core.jobs import JobRegistry, Job

        conn, lock = conn_lock
        lm = LeaseManager(conn, lock)
        registry = JobRegistry(conn, lock, lm)

        class NopJob(Job):
            name = "nop"
            def execute(self, payload):
                return {}

        registry.register(NopJob)
        registry.run("nop")
        health = registry.get_health()
        assert "nop" in health["job_status"]
        assert health["job_status"]["nop"]["last_status"] == "completed"


# =========================================================================
# Phase 7: RRF Fusion
# =========================================================================

class TestRRFFusion:

    def test_basic_fusion(self):
        from dhee.core.fusion_v3 import RRFFusion, FusionCandidate, FusionConfig

        raw = [
            FusionCandidate(
                row_id="r1", source_kind="raw", source_type="event",
                source_id="e1", retrieval_text="raw fact", raw_score=0.9,
            ),
            FusionCandidate(
                row_id="r2", source_kind="raw", source_type="event",
                source_id="e2", retrieval_text="raw fact 2", raw_score=0.7,
            ),
        ]
        distilled = [
            FusionCandidate(
                row_id="d1", source_kind="distilled", source_type="belief",
                source_id="b1", retrieval_text="distilled belief", raw_score=0.85,
                confidence=0.9,
            ),
        ]

        fusion = RRFFusion(FusionConfig(final_top_n=5))
        results, breakdown = fusion.fuse(raw, distilled, query="test")

        assert len(results) >= 1
        assert breakdown.final_count >= 1
        # Distilled should rank high due to higher weight
        top = results[0]
        assert top.source_kind == "distilled"

    def test_staleness_penalty(self):
        from dhee.core.fusion_v3 import RRFFusion, FusionCandidate

        fresh = FusionCandidate(
            row_id="f1", source_kind="distilled", source_type="belief",
            source_id="b1", retrieval_text="fresh", raw_score=0.8,
            status="active",
        )
        stale = FusionCandidate(
            row_id="s1", source_kind="distilled", source_type="belief",
            source_id="b2", retrieval_text="stale", raw_score=0.85,
            status="stale",
        )

        fusion = RRFFusion()
        results, _ = fusion.fuse([], [fresh, stale])

        # Fresh should beat stale despite lower raw score
        assert results[0].row_id == "f1"

    def test_invalidated_excluded(self):
        from dhee.core.fusion_v3 import RRFFusion, FusionCandidate

        valid = FusionCandidate(
            row_id="v1", source_kind="distilled", source_type="belief",
            source_id="b1", retrieval_text="valid", raw_score=0.5,
        )
        invalid = FusionCandidate(
            row_id="i1", source_kind="distilled", source_type="belief",
            source_id="b2", retrieval_text="invalidated", raw_score=0.9,
            status="invalidated",
        )

        fusion = RRFFusion()
        results, _ = fusion.fuse([], [valid, invalid])

        # Invalidated should have score=0 and sort last
        ids = [r.row_id for r in results if r.adjusted_score > 0]
        assert "i1" not in ids

    def test_contradiction_penalty(self):
        from dhee.core.fusion_v3 import RRFFusion, FusionCandidate

        clean = FusionCandidate(
            row_id="c1", source_kind="distilled", source_type="belief",
            source_id="b1", retrieval_text="clean", raw_score=0.8,
        )
        conflicted = FusionCandidate(
            row_id="c2", source_kind="distilled", source_type="belief",
            source_id="b2", retrieval_text="conflicted", raw_score=0.85,
        )

        def checker(t, i):
            return i == "b2"  # b2 has conflicts

        fusion = RRFFusion()
        results, _ = fusion.fuse([], [clean, conflicted], conflict_checker=checker)
        assert results[0].row_id == "c1"  # clean beats conflicted

    def test_breakdown_logged(self):
        from dhee.core.fusion_v3 import RRFFusion, FusionCandidate

        raw = [FusionCandidate(
            row_id="r1", source_kind="raw", source_type="event",
            source_id="e1", retrieval_text="t", raw_score=0.5,
        )]
        dist = [FusionCandidate(
            row_id="d1", source_kind="distilled", source_type="belief",
            source_id="b1", retrieval_text="t", raw_score=0.5,
        )]

        fusion = RRFFusion()
        _, breakdown = fusion.fuse(raw, dist, query="test query")

        d = breakdown.to_dict()
        assert "per_index_counts" in d
        assert d["per_index_counts"]["raw"] == 1
        assert d["per_index_counts"]["distilled"] == 1


# =========================================================================
# Phase 8: Three-Tier Invalidation
# =========================================================================

class TestInvalidation:

    def test_hard_invalidation_sole_source(self, store):
        from dhee.core.invalidation import InvalidationEngine

        e = store.events.add(content="false memory", user_id="u1")
        bid = store.beliefs.add(user_id="u1", claim="false claim")
        store.lineage.add("belief", bid, e.event_id, contribution_weight=1.0)

        engine = InvalidationEngine(
            lineage=store.lineage,
            stores={"belief": store.beliefs},
            conn=store.events._conn,
            lock=store.events._lock,
        )

        # Delete the source → hard invalidation
        store.events.delete(e.event_id)
        result = engine.on_event_deleted(e.event_id)

        assert len(result["hard_invalidated"]) == 1
        belief = store.beliefs.get(bid)
        assert belief["status"] == "invalidated"

    def test_soft_invalidation_sole_source(self, store):
        from dhee.core.invalidation import InvalidationEngine

        e = store.events.add(content="old fact", user_id="u1")
        bid = store.beliefs.add(user_id="u1", claim="old fact", confidence=0.8)
        store.lineage.add("belief", bid, e.event_id, contribution_weight=1.0)

        engine = InvalidationEngine(
            lineage=store.lineage,
            stores={"belief": store.beliefs},
            conn=store.events._conn,
            lock=store.events._lock,
        )

        # Correct the source → soft invalidation
        store.events.correct(e.event_id, "new fact")
        result = engine.on_event_corrected(e.event_id)

        assert len(result["soft_invalidated"]) == 1
        assert len(result["jobs_enqueued"]) >= 1
        belief = store.beliefs.get(bid)
        assert belief["status"] == "stale"

    def test_partial_invalidation_minor_source(self, store):
        from dhee.core.invalidation import InvalidationEngine

        e1 = store.events.add(content="main fact", user_id="u1")
        e2 = store.events.add(content="supporting detail", user_id="u1")

        bid = store.beliefs.add(user_id="u1", claim="combined claim", confidence=0.8)
        store.lineage.add("belief", bid, e1.event_id, contribution_weight=0.8)
        store.lineage.add("belief", bid, e2.event_id, contribution_weight=0.2)

        engine = InvalidationEngine(
            lineage=store.lineage,
            stores={"belief": store.beliefs},
            conn=store.events._conn,
            lock=store.events._lock,
        )

        # Correct the minor source (weight=0.2 < 0.3 threshold)
        store.events.correct(e2.event_id, "updated detail")
        result = engine.on_event_corrected(e2.event_id)

        assert len(result["partial_invalidated"]) == 1
        belief = store.beliefs.get(bid)
        assert belief["status"] == "suspect"

    def test_partial_escalates_on_high_weight(self, store):
        from dhee.core.invalidation import InvalidationEngine

        e1 = store.events.add(content="main", user_id="u1")
        e2 = store.events.add(content="secondary", user_id="u1")

        bid = store.beliefs.add(user_id="u1", claim="test", confidence=0.8)
        store.lineage.add("belief", bid, e1.event_id, contribution_weight=0.6)
        store.lineage.add("belief", bid, e2.event_id, contribution_weight=0.4)

        engine = InvalidationEngine(
            lineage=store.lineage,
            stores={"belief": store.beliefs},
            conn=store.events._conn,
            lock=store.events._lock,
        )

        # Correct the secondary source (weight=0.4 >= 0.3 threshold) → soft
        store.events.correct(e2.event_id, "updated secondary")
        result = engine.on_event_corrected(e2.event_id)

        assert len(result["soft_invalidated"]) == 1  # escalated to soft
        assert len(result["partial_invalidated"]) == 0


# =========================================================================
# Phase 8: Conflicts
# =========================================================================

class TestConflicts:

    def test_create_and_auto_resolve(self, conn_lock):
        from dhee.core.conflicts import ConflictStore

        conn, lock = conn_lock
        cs = ConflictStore(conn, lock)

        # Clear confidence gap → auto-resolve
        result = cs.create(
            "belief_contradiction",
            "belief", "b1", "belief", "b2",
            side_a_confidence=0.95,
            side_b_confidence=0.1,
        )
        assert result["resolution_status"] == "auto_resolved"
        assert result["auto_resolution"]["winner"] == "side_a"

    def test_no_auto_resolve_when_close(self, conn_lock):
        from dhee.core.conflicts import ConflictStore

        conn, lock = conn_lock
        cs = ConflictStore(conn, lock)

        result = cs.create(
            "belief_contradiction",
            "belief", "b1", "belief", "b2",
            side_a_confidence=0.6,
            side_b_confidence=0.5,
        )
        assert result["resolution_status"] == "open"

    def test_manual_resolve(self, conn_lock):
        from dhee.core.conflicts import ConflictStore

        conn, lock = conn_lock
        cs = ConflictStore(conn, lock)

        result = cs.create(
            "anchor_disagreement",
            "anchor", "a1", "anchor", "a2",
        )
        cid = result["conflict_id"]
        assert cs.resolve(cid, {"winner": "a1", "reason": "user chose"})

        conflict = cs.get(cid)
        assert conflict["resolution_status"] == "user_resolved"

    def test_has_open_conflicts(self, conn_lock):
        from dhee.core.conflicts import ConflictStore

        conn, lock = conn_lock
        cs = ConflictStore(conn, lock)

        cs.create("belief_contradiction", "belief", "b1", "belief", "b2")
        assert cs.has_open_conflicts("belief", "b1") is True
        assert cs.has_open_conflicts("belief", "b999") is False

    def test_count_open(self, conn_lock):
        from dhee.core.conflicts import ConflictStore

        conn, lock = conn_lock
        cs = ConflictStore(conn, lock)

        cs.create("belief_contradiction", "belief", "x1", "belief", "x2")
        cs.create("distillation_conflict", "insight", "i1", "insight", "i2")
        assert cs.count_open() == 2


# =========================================================================
# Phase 6: Read Model
# =========================================================================

class TestReadModel:

    def test_refresh_and_query(self, store):
        from dhee.core.read_model import ReadModel

        conn = store.events._conn
        lock = store.events._lock
        rm = ReadModel(conn, lock)

        # Populate
        store.events.add(content="raw fact 1", user_id="u1")
        store.events.add(content="raw fact 2", user_id="u1")
        store.beliefs.add(user_id="u1", claim="belief 1", confidence=0.8)

        counts = rm.refresh(
            "u1",
            events_store=store.events,
            beliefs_store=store.beliefs,
        )
        assert counts["raw_events"] == 2
        assert counts["beliefs"] == 1

        results = rm.query("u1")
        assert len(results) == 3

        # Filter by kind
        raw_only = rm.query("u1", source_kind="raw")
        assert len(raw_only) == 2

    def test_delta_overlay(self, store):
        from dhee.core.read_model import ReadModel

        conn = store.events._conn
        lock = store.events._lock
        rm = ReadModel(conn, lock)

        # Add event before refresh
        store.events.add(content="before refresh", user_id="u1")
        rm.refresh("u1", events_store=store.events)

        # Add event after refresh
        since = rm.last_refresh
        store.events.add(content="after refresh", user_id="u1")

        delta = rm.get_delta("u1", since, events_store=store.events)
        assert len(delta) == 1
        assert delta[0]["retrieval_text"] == "after refresh"

    def test_invalidated_excluded(self, store):
        from dhee.core.read_model import ReadModel

        conn = store.events._conn
        lock = store.events._lock
        rm = ReadModel(conn, lock)

        bid = store.beliefs.add(user_id="u1", claim="will invalidate")
        store.beliefs.set_status(bid, "invalidated")

        rm.refresh("u1", beliefs_store=store.beliefs)
        results = rm.query("u1")
        # Invalidated beliefs are skipped during refresh
        assert all(r["source_id"] != bid for r in results)


# =========================================================================
# Phase 9: Observability
# =========================================================================

class TestV3Health:

    def test_health_metrics(self, store):
        from dhee.core.v3_health import v3_health

        conn = store.events._conn
        lock = store.events._lock

        store.events.add(content="fact", user_id="u1")
        bid = store.beliefs.add(user_id="u1", claim="test")
        store.beliefs.set_status(bid, "stale")

        health = v3_health(conn, lock, user_id="u1")

        assert health["raw_events_active"] == 1
        assert health["derived_invalidation"]["beliefs"]["stale"] == 1
        assert "v3_warnings" in health

    def test_health_no_user_filter(self, store):
        from dhee.core.v3_health import v3_health

        conn = store.events._conn
        lock = store.events._lock

        store.events.add(content="a", user_id="u1")
        store.events.add(content="b", user_id="u2")

        health = v3_health(conn, lock)
        assert health["raw_events_active"] == 2


# =========================================================================
# Phase 10: Migration
# =========================================================================

class TestMigration:

    def test_dual_write(self, db_path):
        from dhee.core.v3_migration import V3MigrationBridge

        cs = CognitionStore(db_path=db_path)
        bridge = V3MigrationBridge(v3_store=cs)

        eid = bridge.on_remember("test fact", "u1", v2_memory_id="v2-123")
        assert eid is not None

        event = cs.events.get(eid)
        assert event.content == "test fact"
        assert event.metadata.get("v2_memory_id") == "v2-123"
        cs.close()

    def test_backfill(self, db_path):
        from dhee.core.v3_migration import V3MigrationBridge

        cs = CognitionStore(db_path=db_path)
        bridge = V3MigrationBridge(v3_store=cs)

        v2_memories = [
            {"memory": "fact 1", "id": "m1", "layer": "sml"},
            {"memory": "fact 2", "id": "m2", "layer": "lml"},
            {"memory": "fact 1", "id": "m3"},  # duplicate content
        ]

        stats = bridge.backfill_from_v2(v2_memories, user_id="u1")
        assert stats["created"] == 2
        assert stats["skipped_dedup"] == 1
        assert stats["total"] == 3

        # Idempotent — running again should skip all
        stats2 = bridge.backfill_from_v2(v2_memories, user_id="u1")
        assert stats2["created"] == 0
        # All 3 are deduped: 2 unique already exist + 1 duplicate content
        assert stats2["skipped_dedup"] == 3
        cs.close()

    def test_correction_bridge(self, db_path):
        from dhee.core.v3_migration import V3MigrationBridge

        cs = CognitionStore(db_path=db_path)
        bridge = V3MigrationBridge(v3_store=cs)

        # First add the original
        bridge.on_remember("I live in Ghazipur", "u1")

        # Then correct
        eid = bridge.on_correction("I live in Ghazipur", "I live in Bengaluru", "u1")
        assert eid is not None

        event = cs.events.get(eid)
        assert event.content == "I live in Bengaluru"
        assert event.supersedes_event_id is not None
        cs.close()

    def test_disabled_bridge(self):
        from dhee.core.v3_migration import V3MigrationBridge
        bridge = V3MigrationBridge(v3_store=None)
        assert bridge.on_remember("test", "u1") is None
        assert bridge.should_use_v3_read() is False


# =========================================================================
# Phase 3: Sparse to_dict
# =========================================================================

class TestSparseDict:

    def test_sparse_omits_empty(self):
        from dhee.core.engram import UniversalEngram

        e = UniversalEngram(
            id="test-1",
            raw_content="hello",
            strength=1.0,
            user_id="u1",
        )
        full = e.to_dict()
        sparse = e.to_dict(sparse=True)

        # Sparse should be smaller
        assert len(sparse) < len(full)
        # Should keep non-empty values
        assert sparse["id"] == "test-1"
        assert sparse["raw_content"] == "hello"
        # Should omit empty lists, None, empty strings
        assert "echo" not in sparse or sparse.get("echo") != []

    def test_full_preserves_all(self):
        from dhee.core.engram import UniversalEngram

        e = UniversalEngram(id="test-2", raw_content="x")
        full = e.to_dict()
        assert "echo" in full  # even empty values
        assert "entities" in full
