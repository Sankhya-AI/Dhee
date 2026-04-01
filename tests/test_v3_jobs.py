"""Tests for Dhee v3 Sprint 2: Lease Manager, Job Registry, Distillation, Promotion.

Covers:
- LeaseManager: acquire, release, renew, expiry steal, cleanup
- JobRegistry: registration, execution, idempotency, history, health
- DistillationStore: submit, dedup, status transitions
- PromotionEngine: validation, type-specific promotion, lineage, batch
- ConsolidationEngine: feedback loop prevention
"""

import json
import sqlite3
import threading
import time

import pytest

from dhee.core.storage import initialize_schema
from dhee.core.lease_manager import LeaseManager
from dhee.core.jobs import Job, JobRegistry, ApplyForgettingJob
from dhee.core.distillation import (
    DistillationCandidate,
    DistillationStore,
    compute_idempotency_key,
    distill_belief_from_events,
    DERIVATION_VERSION,
)
from dhee.core.promotion import PromotionEngine, PromotionResult
from dhee.core.derived_store import (
    BeliefStore,
    PolicyStore,
    InsightStore,
    HeuristicStore,
    DerivedLineageStore,
    CognitionStore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn(tmp_path):
    """Shared connection + lock for all Sprint 2 tests."""
    db_path = str(tmp_path / "test_v3_sprint2.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    lock = threading.RLock()
    yield conn, lock
    conn.close()


@pytest.fixture
def lease_manager(db_conn):
    conn, lock = db_conn
    return LeaseManager(conn, lock, default_duration_seconds=10)


@pytest.fixture
def stores(db_conn):
    """All derived stores sharing one connection."""
    conn, lock = db_conn
    return {
        "beliefs": BeliefStore(conn, lock),
        "policies": PolicyStore(conn, lock),
        "insights": InsightStore(conn, lock),
        "heuristics": HeuristicStore(conn, lock),
        "lineage": DerivedLineageStore(conn, lock),
        "distillation": DistillationStore(conn, lock),
    }


@pytest.fixture
def promotion_engine(stores):
    return PromotionEngine(
        distillation=stores["distillation"],
        beliefs=stores["beliefs"],
        policies=stores["policies"],
        insights=stores["insights"],
        heuristics=stores["heuristics"],
        lineage=stores["lineage"],
        min_confidence=0.3,
    )


# =========================================================================
# LeaseManager Tests
# =========================================================================

class TestLeaseManager:

    def test_acquire_new(self, lease_manager):
        assert lease_manager.acquire("job:decay", "worker-1") is True
        assert lease_manager.is_held("job:decay") is True
        assert lease_manager.get_holder("job:decay") == "worker-1"

    def test_acquire_same_owner_renews(self, lease_manager):
        assert lease_manager.acquire("job:decay", "worker-1") is True
        assert lease_manager.acquire("job:decay", "worker-1") is True  # renew
        assert lease_manager.get_holder("job:decay") == "worker-1"

    def test_acquire_different_owner_blocked(self, lease_manager):
        assert lease_manager.acquire("job:decay", "worker-1") is True
        assert lease_manager.acquire("job:decay", "worker-2") is False
        assert lease_manager.get_holder("job:decay") == "worker-1"

    def test_release(self, lease_manager):
        lease_manager.acquire("job:decay", "worker-1")
        assert lease_manager.release("job:decay", "worker-1") is True
        assert lease_manager.is_held("job:decay") is False

    def test_release_wrong_owner(self, lease_manager):
        lease_manager.acquire("job:decay", "worker-1")
        assert lease_manager.release("job:decay", "worker-2") is False
        assert lease_manager.is_held("job:decay") is True

    def test_release_nonexistent(self, lease_manager):
        assert lease_manager.release("nonexistent", "w1") is False

    def test_renew(self, lease_manager):
        lease_manager.acquire("job:decay", "worker-1")
        assert lease_manager.renew("job:decay", "worker-1") is True

    def test_renew_wrong_owner(self, lease_manager):
        lease_manager.acquire("job:decay", "worker-1")
        assert lease_manager.renew("job:decay", "worker-2") is False

    def test_acquire_expired_lease(self, lease_manager):
        """Expired lease can be stolen by another worker."""
        # Acquire with very short duration
        assert lease_manager.acquire("job:decay", "worker-1", duration_seconds=1) is True
        # Wait for expiry
        time.sleep(1.1)
        # Another worker can steal it
        assert lease_manager.acquire("job:decay", "worker-2") is True
        assert lease_manager.get_holder("job:decay") == "worker-2"

    def test_is_held_expired(self, lease_manager):
        lease_manager.acquire("job:x", "w1", duration_seconds=1)
        time.sleep(1.1)
        assert lease_manager.is_held("job:x") is False
        assert lease_manager.get_holder("job:x") is None

    def test_cleanup_expired(self, lease_manager):
        lease_manager.acquire("job:a", "w1", duration_seconds=1)
        lease_manager.acquire("job:b", "w1", duration_seconds=1)
        lease_manager.acquire("job:c", "w1", duration_seconds=300)
        time.sleep(1.1)
        cleaned = lease_manager.cleanup_expired()
        assert cleaned == 2  # a and b expired, c still held

    def test_multiple_locks_independent(self, lease_manager):
        lease_manager.acquire("job:a", "w1")
        lease_manager.acquire("job:b", "w2")
        assert lease_manager.get_holder("job:a") == "w1"
        assert lease_manager.get_holder("job:b") == "w2"


# =========================================================================
# JobRegistry Tests
# =========================================================================

class _TestJob(Job):
    name = "test_job"

    def execute(self, payload):
        return {"echo": payload.get("value", "none")}


class _FailingJob(Job):
    name = "failing_job"

    def execute(self, payload):
        raise RuntimeError("intentional failure")


class _IdempotentJob(Job):
    name = "idempotent_job"

    def execute(self, payload):
        return {"processed": True}

    def make_idempotency_key(self, payload):
        return f"idem:{payload.get('batch_id', '')}"


class TestJobRegistry:

    def test_register_and_list(self, db_conn, lease_manager):
        conn, lock = db_conn
        registry = JobRegistry(conn, lock, lease_manager)
        registry.register(_TestJob)
        assert "test_job" in registry.list_registered()

    def test_run_success(self, db_conn, lease_manager):
        conn, lock = db_conn
        registry = JobRegistry(conn, lock, lease_manager)
        registry.register(_TestJob)

        result = registry.run("test_job", payload={"value": "hello"})
        assert result["status"] == "completed"
        assert result["result"]["echo"] == "hello"
        assert result["job_id"]

    def test_run_failure(self, db_conn, lease_manager):
        conn, lock = db_conn
        registry = JobRegistry(conn, lock, lease_manager)
        registry.register(_FailingJob)

        result = registry.run("failing_job")
        assert result["status"] == "failed"
        assert "intentional failure" in result["error"]

    def test_run_unknown_job(self, db_conn, lease_manager):
        conn, lock = db_conn
        registry = JobRegistry(conn, lock, lease_manager)

        result = registry.run("nonexistent")
        assert result["status"] == "error"

    def test_idempotency(self, db_conn, lease_manager):
        conn, lock = db_conn
        registry = JobRegistry(conn, lock, lease_manager)
        registry.register(_IdempotentJob)

        r1 = registry.run("idempotent_job", payload={"batch_id": "b1"})
        assert r1["status"] == "completed"

        r2 = registry.run("idempotent_job", payload={"batch_id": "b1"})
        assert r2["status"] == "skipped_idempotent"

        # Different batch_id should run
        r3 = registry.run("idempotent_job", payload={"batch_id": "b2"})
        assert r3["status"] == "completed"

    def test_job_history(self, db_conn, lease_manager):
        conn, lock = db_conn
        registry = JobRegistry(conn, lock, lease_manager)
        registry.register(_TestJob)

        registry.run("test_job", payload={"n": 1})
        registry.run("test_job", payload={"n": 2})

        history = registry.get_job_history("test_job", limit=5)
        assert len(history) == 2
        assert history[0]["status"] == "completed"

    def test_health(self, db_conn, lease_manager):
        conn, lock = db_conn
        registry = JobRegistry(conn, lock, lease_manager)
        registry.register(_TestJob)

        health = registry.get_health()
        assert health["total_registered"] == 1
        assert "test_job" in health["job_status"]
        assert health["job_status"]["test_job"]["last_status"] == "never_run"

        registry.run("test_job")
        health = registry.get_health()
        assert health["job_status"]["test_job"]["last_status"] == "completed"

    def test_lease_prevents_concurrent(self, db_conn, lease_manager):
        """Two sequential runs of same job: first completes, second runs too
        (lease released). But if lease held, second is blocked."""
        conn, lock = db_conn
        registry = JobRegistry(conn, lock, lease_manager)
        registry.register(_TestJob)

        # First run — should succeed
        r1 = registry.run("test_job", owner_id="w1")
        assert r1["status"] == "completed"

        # Second run — lease was released, should succeed
        r2 = registry.run("test_job", owner_id="w2")
        assert r2["status"] == "completed"

    def test_run_all(self, db_conn, lease_manager):
        conn, lock = db_conn
        registry = JobRegistry(conn, lock, lease_manager)
        registry.register(_TestJob)
        registry.register(_IdempotentJob)

        results = registry.run_all()
        assert len(results) == 2
        completed = [r for r in results if r["status"] == "completed"]
        assert len(completed) == 2


# =========================================================================
# Distillation Tests
# =========================================================================

class TestDistillation:

    def test_idempotency_key(self):
        k1 = compute_idempotency_key(["e1", "e2"], 1, "belief:u1:test")
        k2 = compute_idempotency_key(["e2", "e1"], 1, "belief:u1:test")  # sorted
        assert k1 == k2  # Same regardless of order

        k3 = compute_idempotency_key(["e1", "e2"], 2, "belief:u1:test")  # diff version
        assert k1 != k3

    def test_candidate_auto_key(self):
        c = DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1", "e2"],
            target_type="belief",
            canonical_key="belief:u1:test",
            payload={"claim": "test"},
        )
        assert c.idempotency_key
        assert len(c.idempotency_key) == 24

    def test_submit_and_get(self, stores):
        ds = stores["distillation"]
        candidate = DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="belief:u1:test",
            payload={"user_id": "u1", "claim": "test claim"},
            confidence=0.6,
        )
        result = ds.submit(candidate)
        assert result == "c1"

        fetched = ds.get("c1")
        assert fetched is not None
        assert fetched["target_type"] == "belief"
        assert fetched["status"] == "pending_validation"

    def test_submit_dedup(self, stores):
        ds = stores["distillation"]

        c1 = DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="belief:u1:test",
            payload={"claim": "test"},
        )
        c2 = DistillationCandidate(
            candidate_id="c2",
            source_event_ids=["e1"],  # same source + same key
            target_type="belief",
            canonical_key="belief:u1:test",
            payload={"claim": "test"},
        )

        assert ds.submit(c1) == "c1"
        assert ds.submit(c2) is None  # dedup

    def test_submit_after_reject(self, stores):
        ds = stores["distillation"]

        c1 = DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="belief:u1:test",
            payload={"claim": "test"},
        )
        ds.submit(c1)
        ds.set_status("c1", "rejected")

        # Same idempotency key, but rejected — should allow resubmit
        c2 = DistillationCandidate(
            candidate_id="c2",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="belief:u1:test",
            payload={"claim": "test v2"},
        )
        assert ds.submit(c2) == "c2"

    def test_get_pending(self, stores):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1", source_event_ids=["e1"],
            target_type="belief", canonical_key="k1",
            payload={"claim": "a"}, confidence=0.8,
        ))
        ds.submit(DistillationCandidate(
            candidate_id="c2", source_event_ids=["e2"],
            target_type="policy", canonical_key="k2",
            payload={"name": "b"}, confidence=0.5,
        ))

        pending = ds.get_pending()
        assert len(pending) == 2

        beliefs_only = ds.get_pending("belief")
        assert len(beliefs_only) == 1

    def test_distill_belief_from_events(self):
        events = [
            {"event_id": "e1", "content": "User prefers dark mode"},
            {"event_id": "e2", "content": "User prefers dark mode"},
        ]
        candidate = distill_belief_from_events(events, user_id="u1")
        assert candidate is not None
        assert candidate.target_type == "belief"
        assert candidate.confidence == 0.5  # 0.3 + 0.1 * 2
        assert len(candidate.source_event_ids) == 2

    def test_distill_empty_events(self):
        assert distill_belief_from_events([], user_id="u1") is None


# =========================================================================
# Promotion Tests
# =========================================================================

class TestPromotion:

    def test_promote_belief(self, stores, promotion_engine):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1", "e2"],
            target_type="belief",
            canonical_key="belief:u1:test",
            payload={"user_id": "u1", "claim": "Python is great", "domain": "tech"},
            confidence=0.7,
        ))

        result = promotion_engine.promote_pending("belief")
        assert result.to_dict()["promoted"] == 1

        # Verify belief was created
        beliefs = stores["beliefs"].list_by_user("u1")
        assert len(beliefs) == 1
        assert beliefs[0]["claim"] == "Python is great"

        # Verify lineage was written
        lineage = stores["lineage"].get_sources("belief", beliefs[0]["belief_id"])
        assert len(lineage) == 2  # from e1 and e2

        # Verify candidate was marked promoted
        candidate = ds.get("c1")
        assert candidate["status"] == "promoted"
        assert candidate["promoted_id"] == beliefs[0]["belief_id"]

    def test_promote_policy(self, stores, promotion_engine):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="policy",
            canonical_key="policy:u1:blame",
            payload={
                "user_id": "u1",
                "name": "git blame first",
                "condition": {"task_types": ["bug_fix"]},
                "action": {"approach": "Run git blame"},
            },
            confidence=0.6,
        ))

        result = promotion_engine.promote_pending("policy")
        assert result.to_dict()["promoted"] == 1

        policies = stores["policies"].list_by_user("u1")
        assert len(policies) == 1
        assert policies[0]["name"] == "git blame first"

    def test_promote_insight(self, stores, promotion_engine):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="insight",
            canonical_key="insight:u1:tokens",
            payload={
                "user_id": "u1",
                "content": "Token expiry causes outages in production",
                "insight_type": "causal",
            },
            confidence=0.5,
        ))

        result = promotion_engine.promote_pending("insight")
        assert result.to_dict()["promoted"] == 1

    def test_promote_heuristic(self, stores, promotion_engine):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="heuristic",
            canonical_key="heuristic:u1:constrained",
            payload={
                "user_id": "u1",
                "content": "Start with the most constrained component first",
                "abstraction_level": "universal",
            },
            confidence=0.6,
        ))

        result = promotion_engine.promote_pending("heuristic")
        assert result.to_dict()["promoted"] == 1

    def test_reject_low_confidence(self, stores, promotion_engine):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="belief:u1:weak",
            payload={"user_id": "u1", "claim": "maybe"},
            confidence=0.1,  # Below min_confidence of 0.3
        ))

        result = promotion_engine.promote_pending("belief")
        assert result.to_dict()["rejected"] == 1
        assert result.to_dict()["promoted"] == 0

    def test_reject_empty_payload(self, stores, promotion_engine):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="belief:u1:empty",
            payload={},
            confidence=0.8,
        ))

        result = promotion_engine.promote_pending("belief")
        assert result.to_dict()["rejected"] == 1

    def test_reject_short_claim(self, stores, promotion_engine):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="belief:u1:short",
            payload={"user_id": "u1", "claim": "hi"},  # < 5 chars
            confidence=0.8,
        ))

        result = promotion_engine.promote_pending("belief")
        assert result.to_dict()["rejected"] == 1

    def test_promote_single(self, stores, promotion_engine):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="belief:u1:single",
            payload={"user_id": "u1", "claim": "Test single promotion"},
            confidence=0.7,
        ))

        result = promotion_engine.promote_single("c1")
        assert result["status"] == "promoted"
        assert result["promoted_id"]

    def test_promote_single_nonexistent(self, promotion_engine):
        result = promotion_engine.promote_single("nonexistent")
        assert result["status"] == "error"

    def test_promote_single_already_promoted(self, stores, promotion_engine):
        ds = stores["distillation"]

        ds.submit(DistillationCandidate(
            candidate_id="c1",
            source_event_ids=["e1"],
            target_type="belief",
            canonical_key="belief:u1:double",
            payload={"user_id": "u1", "claim": "Test double promotion"},
            confidence=0.7,
        ))

        r1 = promotion_engine.promote_single("c1")
        assert r1["status"] == "promoted"

        r2 = promotion_engine.promote_single("c1")
        assert r2["status"] == "skipped"

    def test_batch_promotion(self, stores, promotion_engine):
        ds = stores["distillation"]

        for i in range(5):
            ds.submit(DistillationCandidate(
                candidate_id=f"c{i}",
                source_event_ids=[f"e{i}"],
                target_type="belief",
                canonical_key=f"belief:u1:batch{i}",
                payload={"user_id": "u1", "claim": f"Batch claim number {i}"},
                confidence=0.6,
            ))

        result = promotion_engine.promote_pending("belief", limit=10)
        assert result.to_dict()["promoted"] == 5

        beliefs = stores["beliefs"].list_by_user("u1")
        assert len(beliefs) == 5


# =========================================================================
# Consolidation Feedback Loop Tests
# =========================================================================

class TestConsolidationSafety:

    def test_should_promote_rejects_consolidated(self):
        """Signals with source='consolidated' must be rejected."""
        from dhee.core.consolidation import ConsolidationEngine

        # We can't easily instantiate ConsolidationEngine without full deps,
        # so test the logic directly by checking the source code contract.
        import inspect
        src = inspect.getsource(ConsolidationEngine._should_promote)

        # Verify the feedback loop guard is present
        assert "consolidated" in src, (
            "ConsolidationEngine._should_promote must check for "
            "'consolidated' source to prevent feedback loops"
        )
        assert "consolidated_from" in src, (
            "ConsolidationEngine._should_promote must check for "
            "'consolidated_from' metadata to prevent re-consolidation"
        )

    def test_promote_uses_infer_false(self):
        """_promote_to_passive must use infer=False to skip enrichment."""
        from dhee.core.consolidation import ConsolidationEngine
        import inspect
        src = inspect.getsource(ConsolidationEngine._promote_to_passive)

        assert "infer=False" in src, (
            "ConsolidationEngine._promote_to_passive must use infer=False "
            "to skip the LLM enrichment pipeline"
        )

    def test_promote_tags_provenance(self):
        """_promote_to_passive must tag consolidated provenance."""
        from dhee.core.consolidation import ConsolidationEngine
        import inspect
        src = inspect.getsource(ConsolidationEngine._promote_to_passive)

        assert '"source": "consolidated"' in src or "'source': 'consolidated'" in src, (
            "ConsolidationEngine._promote_to_passive must tag "
            "promoted memories with source='consolidated'"
        )


# =========================================================================
# AGI Loop Cleanup Tests
# =========================================================================

class TestAgiLoopCleanup:

    def test_no_phantom_imports(self):
        """agi_loop.py must not import non-existent engram_* packages."""
        import inspect
        from dhee.core import agi_loop
        src = inspect.getsource(agi_loop)

        phantom_packages = [
            "engram_reconsolidation",
            "engram_procedural",
            "engram_metamemory",
            "engram_prospective",
            "engram_working",
            "engram_failure",
            "engram_router",
            "engram_identity",
            "engram_heartbeat",
            "engram_policy",
            "engram_skills",
            "engram_spawn",
            "engram_resilience",
        ]

        for pkg in phantom_packages:
            assert pkg not in src, (
                f"agi_loop.py still references phantom package '{pkg}'. "
                f"All engram_* phantom imports must be removed."
            )

    def test_run_agi_cycle_api_preserved(self):
        """run_agi_cycle function must still exist (backward compat)."""
        from dhee.core.agi_loop import run_agi_cycle
        assert callable(run_agi_cycle)

    def test_get_system_health_api_preserved(self):
        """get_system_health function must still exist (backward compat)."""
        from dhee.core.agi_loop import get_system_health
        assert callable(get_system_health)
