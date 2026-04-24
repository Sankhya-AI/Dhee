"""M4.2 regression — online MetaBuddhi loop + downstream-success tier bumps.

Plan reference: encapsulated-rolling-bengio.md, Movement 4.

These tests lock in the wiring that closes the propose → assess →
commit/rollback loop at the EvolutionLayer boundary:

  * ``on_answer_generated`` feeds a positive structured evaluation signal
    into MetaBuddhi (not a hardcoded 1.0),
    and, when a substrate DB is attached, stamps ``last_verified_at`` and
    bumps the tier of each cited fact via ``promote_on_downstream_success``.
  * ``on_answer_corrected`` feeds a negative structured evaluation signal
    and leaves
    tiers + verification stamps untouched.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

from dhee.core.engram_verification import staleness_days
from dhee.core.evolution import EvolutionLayer
from dhee.core.resolvers import ContextResolver
from dhee.db.sqlite import FullSQLiteManager


def _fact(subject, predicate, value):
    return SimpleNamespace(
        subject=subject,
        predicate=predicate,
        value=value,
        value_numeric=None,
        value_unit=None,
        time=None,
        valid_from=None,
        valid_until=None,
        qualifier=None,
        canonical_key=None,
        confidence=1.0,
        is_derived=False,
    )


def _engram(fact):
    return SimpleNamespace(
        context=SimpleNamespace(
            has_context=lambda: False,
            era=None, place=None, place_type=None, place_detail=None,
            time_absolute=None, time_markers=[],
            time_range_start=None, time_range_end=None,
            time_derivation=None, activity=None,
            session_id=None, session_position=0,
        ),
        scene=SimpleNamespace(
            setting=None, people_present=[], self_state=None,
            emotional_tone=None, sensory_cues=[],
        ),
        facts=[fact], entities=[], links=[],
    )


@pytest.fixture()
def evo_and_db(tmp_path):
    db = FullSQLiteManager(str(tmp_path / "t.db"))
    with db._get_connection() as conn:
        for mid in ("m1", "m2"):
            conn.execute(
                "INSERT INTO memories (id, memory, user_id) VALUES (?,?,?)",
                (mid, f"mem-{mid}", "u1"),
            )
    r = ContextResolver(db)
    r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
    r.store_engram(_engram(_fact("alice", "works_at", "acme")), "m2")

    evo = EvolutionLayer(
        data_dir=str(tmp_path / "evo"),
        enable_samskara=False,  # keep Samskara out of the unit test
        enable_viveka=False,
        enable_alaya=False,
        enable_nididhyasana=False,
    )
    evo.attach_substrate(db)
    return evo, db


class _StubMetaBuddhi:
    def __init__(self):
        self.scores = []
        self.task_types = []
        self.sources = []
        self.components = []

    def record_evaluation(self, score, *, task_type=None, source=None, signal_components=None):
        self.scores.append(score)
        self.task_types.append(task_type)
        self.sources.append(source)
        self.components.append(signal_components or {})
        return None


def test_accepted_answer_marks_facts_verified(evo_and_db):
    evo, db = evo_and_db
    evo._meta_buddhi = _StubMetaBuddhi()

    evo.on_answer_generated(
        query="what's alice's email?",
        answer="a@x",
        source_memory_ids=["m1"],
        user_id="u1",
    )

    with db._get_connection() as conn:
        row = dict(
            conn.execute(
                "SELECT last_verified_at, tier FROM engram_facts "
                "WHERE memory_id = 'm1'"
            ).fetchone()
        )
    assert row["last_verified_at"] is not None
    assert staleness_days(row) < 1.0


def test_accepted_answer_bumps_tier_on_cited_facts(evo_and_db):
    evo, db = evo_and_db
    evo._meta_buddhi = _StubMetaBuddhi()

    evo.on_answer_generated(
        query="alice email?",
        answer="a@x",
        source_memory_ids=["m1"],
        user_id="u1",
    )

    with db._get_connection() as conn:
        rows = {
            r["memory_id"]: r["tier"]
            for r in conn.execute(
                "SELECT memory_id, tier FROM engram_facts"
            ).fetchall()
        }
    # Cited fact jumps medium → high; unrelated fact stays at medium.
    assert rows["m1"] == "high"
    assert rows["m2"] == "medium"


def test_accepted_answer_records_positive_meta_buddhi_signal(evo_and_db):
    evo, _db = evo_and_db
    stub = _StubMetaBuddhi()
    evo._meta_buddhi = stub

    evo.on_answer_generated(
        query="q", answer="a", source_memory_ids=["m1"], user_id="u1",
    )
    assert len(stub.scores) == 1
    assert stub.scores[0] == pytest.approx(0.75)
    assert stub.sources == ["answer_accepted"]
    assert stub.components[0]["accepted"] is True


def test_corrected_answer_records_negative_meta_buddhi_signal(evo_and_db):
    evo, db = evo_and_db
    stub = _StubMetaBuddhi()
    evo._meta_buddhi = stub

    evo.on_answer_corrected(
        query="q",
        wrong_answer="a",
        correct_answer="b",
        memory_ids=["m1"],
        user_id="u1",
    )
    assert len(stub.scores) == 1
    assert stub.scores[0] == pytest.approx(0.15)
    assert stub.sources == ["answer_corrected"]
    assert stub.components[0]["corrected"] is True

    # Correction path must NOT stamp last_verified_at or bump tier
    with db._get_connection() as conn:
        row = dict(
            conn.execute(
                "SELECT last_verified_at, tier FROM engram_facts "
                "WHERE memory_id = 'm1'"
            ).fetchone()
        )
    assert row["last_verified_at"] is None
    assert row["tier"] == "medium"


def test_task_outcome_signal_blends_outcome_and_operational_metadata(evo_and_db):
    evo, _db = evo_and_db
    stub = _StubMetaBuddhi()
    evo._meta_buddhi = stub

    score = evo.record_task_outcome(
        task_type="bug_fix",
        outcome_score=0.8,
        what_worked="narrowed flaky test with isolation",
        metadata={
            "tests_passed": 8,
            "tests_failed": 2,
            "correction_count": 1,
            "reverted": False,
        },
        source="unit_test",
    )

    assert score is not None
    assert len(stub.scores) == 1
    assert stub.scores[0] == pytest.approx(score)
    assert stub.scores[0] > 0.7
    assert stub.sources == ["unit_test"]
    assert stub.components[0]["tests_passed"] == 8
    assert stub.components[0]["tests_failed"] == 2


def test_meta_buddhi_records_five_accepts_resolves_pending_attempt(evo_and_db):
    """End-to-end: _MIN_EVAL_COUNT=5 accepts should push MetaBuddhi off the
    evaluating status and into a resolved decision (promoted or rolled_back).
    """
    evo, _db = evo_and_db

    # Force a real MetaBuddhi (not the stub) and seed a pending attempt.
    from dhee.core.meta_buddhi import MetaBuddhi

    evo._meta_buddhi = MetaBuddhi(
        data_dir=os.path.join(tempfile.mkdtemp(), "meta_buddhi"),
    )
    attempt = evo._meta_buddhi.propose_improvement(
        dimension="keyword_weight",
    )
    assert attempt is not None
    assert attempt.status == "evaluating"

    for _ in range(5):
        evo.on_answer_generated(
            query="q", answer="a", source_memory_ids=["m1"], user_id="u1",
        )

    resolved = evo._meta_buddhi._attempts[attempt.id]
    assert resolved.status in ("promoted", "rolled_back")
    assert resolved.resolved_at is not None
