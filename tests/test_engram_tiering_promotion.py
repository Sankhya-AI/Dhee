"""M3 regression — promotion, consolidator, epistemic verification.

Plan reference: encapsulated-rolling-bengio.md, Movement 3.

These tests lock in the Movement 3 invariants:
  * Reaffirmation + age thresholds drive promotion medium→high→canonical.
  * Canonical is write-once: no sweep, no demotion outside explicit supersede.
  * Dedup fusion keeps the strongest row and merges reaffirmation counts.
  * Forgetting sweep moves (not deletes) avoid-tier rows to the cold archive.
  * ``last_verified_at`` is independent of ``last_reaffirmed_at`` and drives
    the epistemic-check decision.
"""

from __future__ import annotations

import os
import tempfile
import time
from types import SimpleNamespace

import pytest

from dhee.core.engram_consolidator import FORGET_AFTER_DAYS, run_consolidation
from dhee.core.engram_tiering import (
    CANONICAL_MIN_AGE_DAYS,
    CANONICAL_MIN_REAFFIRMED,
    HIGH_MIN_AGE_DAYS,
    HIGH_MIN_REAFFIRMED,
    promote_on_downstream_success,
    run_promotion_pass,
)
from dhee.core.engram_verification import (
    DEFAULT_VERIFY_TTL_DAYS,
    LOAD_BEARING_VERIFY_TTL_DAYS,
    mark_verified,
    needs_epistemic_check,
    pending_epistemic_checks,
    staleness_days,
)
from dhee.core.resolvers import ContextResolver
from dhee.db.sqlite import FullSQLiteManager


def _fact(subject, predicate, value, *, valid_from=None):
    return SimpleNamespace(
        subject=subject,
        predicate=predicate,
        value=value,
        value_numeric=None,
        value_unit=None,
        time=None,
        valid_from=valid_from,
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
            era=None,
            place=None,
            place_type=None,
            place_detail=None,
            time_absolute=None,
            time_markers=[],
            time_range_start=None,
            time_range_end=None,
            time_derivation=None,
            activity=None,
            session_id=None,
            session_position=0,
        ),
        scene=SimpleNamespace(
            setting=None,
            people_present=[],
            self_state=None,
            emotional_tone=None,
            sensory_cues=[],
        ),
        facts=[fact],
        entities=[],
        links=[],
    )


@pytest.fixture()
def fresh_db():
    tmp = tempfile.mkdtemp()
    db = FullSQLiteManager(os.path.join(tmp, "t.db"))
    with db._get_connection() as conn:
        for mid in ("m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8"):
            conn.execute(
                "INSERT INTO memories (id, memory, user_id) VALUES (?,?,?)",
                (mid, f"mem-{mid}", "u1"),
            )
    return db


def _reaffirm(resolver, predicate, value, n, mid_start=1):
    """Write the same fact ``n`` times across distinct memory rows."""
    for i in range(n):
        resolver.store_engram(
            _engram(_fact("alice", predicate, value)),
            f"m{mid_start + i}",
        )


def _backdate_fact(db, days):
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE engram_facts SET created_at = datetime('now', ?)",
            (f"-{days} days",),
        )


class TestPromotionPass:
    def test_medium_to_high_requires_reaffirmation_and_age(self, fresh_db):
        r = ContextResolver(fresh_db)
        _reaffirm(r, "has_email", "a@x", HIGH_MIN_REAFFIRMED + 1)
        _backdate_fact(fresh_db, HIGH_MIN_AGE_DAYS + 1)

        report = run_promotion_pass(fresh_db)
        assert report["engram_facts"]["medium_to_high"] == 1

        with fresh_db._get_connection() as conn:
            row = conn.execute(
                "SELECT tier, reaffirmed_count FROM engram_facts"
            ).fetchone()
        assert row["tier"] == "high"
        assert row["reaffirmed_count"] >= HIGH_MIN_REAFFIRMED

    def test_young_fact_not_promoted_even_when_reaffirmed(self, fresh_db):
        r = ContextResolver(fresh_db)
        _reaffirm(r, "has_email", "a@x", HIGH_MIN_REAFFIRMED + 2)
        # leave created_at fresh — should NOT promote

        report = run_promotion_pass(fresh_db)
        assert report["engram_facts"]["medium_to_high"] == 0
        with fresh_db._get_connection() as conn:
            row = conn.execute("SELECT tier FROM engram_facts").fetchone()
        assert row["tier"] == "medium"

    def test_high_to_canonical_requires_higher_bar(self, fresh_db):
        r = ContextResolver(fresh_db)
        _reaffirm(r, "has_email", "a@x", CANONICAL_MIN_REAFFIRMED + 1)
        _backdate_fact(fresh_db, CANONICAL_MIN_AGE_DAYS + 1)

        # A single pass walks both medium→high and high→canonical when the
        # age/reaffirmation bars are both cleared.
        report = run_promotion_pass(fresh_db)
        assert report["engram_facts"]["medium_to_high"] == 1
        assert report["engram_facts"]["high_to_canonical"] == 1
        with fresh_db._get_connection() as conn:
            row = conn.execute("SELECT tier FROM engram_facts").fetchone()
        assert row["tier"] == "canonical"

    def test_high_needs_canonical_bar_beyond_high_bar(self, fresh_db):
        """Age-qualified for high but not for canonical → stays at high."""
        r = ContextResolver(fresh_db)
        _reaffirm(r, "has_email", "a@x", CANONICAL_MIN_REAFFIRMED + 1)
        # old enough for high, not old enough for canonical
        _backdate_fact(fresh_db, HIGH_MIN_AGE_DAYS + 2)

        run_promotion_pass(fresh_db)
        with fresh_db._get_connection() as conn:
            row = conn.execute("SELECT tier FROM engram_facts").fetchone()
        assert row["tier"] == "high"

    def test_superseded_rows_not_promoted(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "lives_in", "NYC")), "m1")
        r.store_engram(_engram(_fact("alice", "lives_in", "SF")), "m2")
        # inflate reaffirmation counter on the superseded row directly
        with fresh_db._get_connection() as conn:
            conn.execute(
                "UPDATE engram_facts SET reaffirmed_count = 10 "
                "WHERE value='NYC'"
            )
        _backdate_fact(fresh_db, HIGH_MIN_AGE_DAYS + 5)

        run_promotion_pass(fresh_db)
        with fresh_db._get_connection() as conn:
            nyc = conn.execute(
                "SELECT tier FROM engram_facts WHERE value='NYC'"
            ).fetchone()
        # superseded rows stay in 'avoid' regardless of counter
        assert nyc["tier"] == "avoid"


class TestDownstreamPromotion:
    def test_bumps_tier_one_step(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        with fresh_db._get_connection() as conn:
            fid = conn.execute("SELECT id FROM engram_facts").fetchone()["id"]

        assert promote_on_downstream_success(fresh_db, fact_id=fid) is True
        with fresh_db._get_connection() as conn:
            row = conn.execute("SELECT tier FROM engram_facts").fetchone()
        assert row["tier"] == "high"

    def test_noop_on_canonical(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        with fresh_db._get_connection() as conn:
            fid = conn.execute("SELECT id FROM engram_facts").fetchone()["id"]
            conn.execute(
                "UPDATE engram_facts SET tier='canonical' WHERE id=?",
                (fid,),
            )
        # already top tier — must be a no-op, not an error
        assert promote_on_downstream_success(fresh_db, fact_id=fid) is False

    def test_noop_on_superseded(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "lives_in", "NYC")), "m1")
        r.store_engram(_engram(_fact("alice", "lives_in", "SF")), "m2")
        with fresh_db._get_connection() as conn:
            nyc_id = conn.execute(
                "SELECT id FROM engram_facts WHERE value='NYC'"
            ).fetchone()["id"]
        assert promote_on_downstream_success(fresh_db, fact_id=nyc_id) is False


class TestConsolidator:
    def test_forgetting_sweep_archives_avoid_rows(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(
            _engram(_fact("alice", "lives_in", "NYC", valid_from="2023-01-01")),
            "m1",
        )
        r.store_engram(
            _engram(_fact("alice", "lives_in", "SF", valid_from="2025-01-01")),
            "m2",
        )
        # backdate only the superseded row
        with fresh_db._get_connection() as conn:
            conn.execute(
                "UPDATE engram_facts SET created_at = datetime('now', ?) "
                "WHERE tier='avoid'",
                (f"-{FORGET_AFTER_DAYS + 1} days",),
            )

        report = run_consolidation(fresh_db)
        assert report["forgotten"]["engram_facts"] == 1

        with fresh_db._get_connection() as conn:
            live = list(conn.execute("SELECT value FROM engram_facts"))
            arch = list(
                conn.execute(
                    "SELECT canonical_key, reason FROM engram_fact_archive"
                )
            )
        assert [row["value"] for row in live] == ["SF"]
        assert len(arch) == 1
        assert "avoid" in arch[0]["reason"]

    def test_canonical_never_swept(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        with fresh_db._get_connection() as conn:
            # force canonical tier + make old enough to match age bound
            conn.execute(
                "UPDATE engram_facts SET tier='canonical', "
                "created_at = datetime('now', ?)",
                (f"-{FORGET_AFTER_DAYS + 30} days",),
            )

        run_consolidation(fresh_db)
        with fresh_db._get_connection() as conn:
            live = conn.execute(
                "SELECT tier FROM engram_facts"
            ).fetchone()
            arch_count = conn.execute(
                "SELECT COUNT(*) AS n FROM engram_fact_archive"
            ).fetchone()["n"]
        assert live["tier"] == "canonical"
        assert arch_count == 0

    def test_fusion_collapses_duplicate_canonical_keys(self, fresh_db):
        # Same canonical key, two live rows with different ids — force this
        # state by hand since resolver.store_engram would reaffirm instead.
        with fresh_db._get_connection() as conn:
            for idx, tier in enumerate(("medium", "high"), start=1):
                conn.execute(
                    "INSERT INTO engram_facts "
                    "(id, memory_id, subject, predicate, value, canonical_key, "
                    " tier, reaffirmed_count) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        f"dup{idx}",
                        f"m{idx}",
                        "alice",
                        "has_email",
                        "a@x",
                        "alice|has_email",
                        tier,
                        idx,  # medium=1, high=2
                    ),
                )

        report = run_consolidation(fresh_db)
        assert report["fusion"]["engram_facts"] == 1

        with fresh_db._get_connection() as conn:
            keeper = conn.execute(
                "SELECT id, tier, reaffirmed_count FROM engram_facts "
                "WHERE superseded_by_id IS NULL"
            ).fetchone()
            loser = conn.execute(
                "SELECT id, tier, superseded_by_id FROM engram_facts "
                "WHERE superseded_by_id IS NOT NULL"
            ).fetchone()
        # highest tier wins (high > medium)
        assert keeper["tier"] == "high"
        # counts merged (1 + 2)
        assert keeper["reaffirmed_count"] == 3
        # lineage preserved
        assert loser["tier"] == "avoid"
        assert loser["superseded_by_id"] == keeper["id"]

    def test_idempotent(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        run_consolidation(fresh_db)
        # second run on an already-consolidated DB must not raise or churn
        report = run_consolidation(fresh_db)
        assert report["fusion"]["engram_facts"] == 0
        assert report["forgotten"]["engram_facts"] == 0


class TestVerification:
    def test_never_verified_is_None(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        with fresh_db._get_connection() as conn:
            row = dict(conn.execute("SELECT * FROM engram_facts").fetchone())
        assert staleness_days(row) is None

    def test_mark_verified_stamps_timestamp(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        with fresh_db._get_connection() as conn:
            fid = conn.execute("SELECT id FROM engram_facts").fetchone()["id"]
        assert mark_verified(fresh_db, fact_id=fid) is True
        with fresh_db._get_connection() as conn:
            row = dict(conn.execute("SELECT * FROM engram_facts").fetchone())
        assert row["last_verified_at"] is not None
        assert staleness_days(row) < 1.0

    def test_mark_verified_noop_on_superseded(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "lives_in", "NYC")), "m1")
        r.store_engram(_engram(_fact("alice", "lives_in", "SF")), "m2")
        with fresh_db._get_connection() as conn:
            nyc_id = conn.execute(
                "SELECT id FROM engram_facts WHERE value='NYC'"
            ).fetchone()["id"]
        assert mark_verified(fresh_db, fact_id=nyc_id) is False

    def test_needs_check_never_verified_load_bearing(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        with fresh_db._get_connection() as conn:
            row = dict(conn.execute("SELECT * FROM engram_facts").fetchone())
        assert needs_epistemic_check(row, load_bearing=True) is True
        assert needs_epistemic_check(row, load_bearing=False) is False

    def test_needs_check_respects_ttl(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        with fresh_db._get_connection() as conn:
            conn.execute(
                "UPDATE engram_facts SET last_verified_at = ?",
                (time.time() - (DEFAULT_VERIFY_TTL_DAYS + 1) * 86400,),
            )
            row = dict(conn.execute("SELECT * FROM engram_facts").fetchone())
        assert needs_epistemic_check(row, load_bearing=False) is True

    def test_canonical_non_load_bearing_skips_check(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        with fresh_db._get_connection() as conn:
            conn.execute(
                "UPDATE engram_facts SET tier='canonical', last_verified_at = ?",
                (time.time() - 365 * 86400,),
            )
            row = dict(conn.execute("SELECT * FROM engram_facts").fetchone())
        # Canonical gets a pass on non-load-bearing paths regardless of staleness
        assert needs_epistemic_check(row, load_bearing=False) is False
        # But load-bearing paths tighten the TTL and should flip back to True
        assert needs_epistemic_check(row, load_bearing=True) is True

    def test_pending_checks_skips_non_load_bearing_never_verified(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        assert pending_epistemic_checks(fresh_db, user_id="u1") == []

    def test_pending_checks_surfaces_stale(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        with fresh_db._get_connection() as conn:
            conn.execute(
                "UPDATE engram_facts SET last_verified_at = ?",
                (time.time() - (DEFAULT_VERIFY_TTL_DAYS + 5) * 86400,),
            )
        checks = pending_epistemic_checks(fresh_db, user_id="u1")
        assert len(checks) == 1
        assert checks[0]["subject"] == "alice"
        assert checks[0]["staleness_days"] is not None

    def test_pending_checks_scopes_by_user(self, fresh_db):
        r = ContextResolver(fresh_db)
        with fresh_db._get_connection() as conn:
            conn.execute(
                "INSERT INTO memories (id, memory, user_id) VALUES (?,?,?)",
                ("m-u2", "", "u2"),
            )
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        r.store_engram(_engram(_fact("bob", "has_email", "b@x")), "m-u2")
        with fresh_db._get_connection() as conn:
            conn.execute(
                "UPDATE engram_facts SET last_verified_at = ?",
                (time.time() - (DEFAULT_VERIFY_TTL_DAYS + 1) * 86400,),
            )
        u1 = pending_epistemic_checks(fresh_db, user_id="u1")
        u2 = pending_epistemic_checks(fresh_db, user_id="u2")
        assert [c["subject"] for c in u1] == ["alice"]
        assert [c["subject"] for c in u2] == ["bob"]

    def test_pending_checks_excludes_superseded(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "lives_in", "NYC")), "m1")
        r.store_engram(_engram(_fact("alice", "lives_in", "SF")), "m2")
        with fresh_db._get_connection() as conn:
            conn.execute(
                "UPDATE engram_facts SET last_verified_at = ?",
                (time.time() - (DEFAULT_VERIFY_TTL_DAYS + 5) * 86400,),
            )
        checks = pending_epistemic_checks(fresh_db, user_id="u1")
        assert [c["value"] for c in checks] == ["SF"]

    def test_load_bearing_uses_shorter_ttl(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("alice", "has_email", "a@x")), "m1")
        # Between the two TTLs: stale for load-bearing, fresh otherwise
        age_days = (LOAD_BEARING_VERIFY_TTL_DAYS + DEFAULT_VERIFY_TTL_DAYS) / 2
        with fresh_db._get_connection() as conn:
            conn.execute(
                "UPDATE engram_facts SET last_verified_at = ?",
                (time.time() - age_days * 86400,),
            )
            row = dict(conn.execute("SELECT * FROM engram_facts").fetchone())
        assert needs_epistemic_check(row, load_bearing=False) is False
        assert needs_epistemic_check(row, load_bearing=True) is True
