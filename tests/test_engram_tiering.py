"""M2 regression — tiering, supersede chains, preferences routing.

Plan reference: encapsulated-rolling-bengio.md, Movement 2.

These tests hit the substrate directly so a future refactor of
``ContextResolver.store_engram`` can't regress the invariants silently.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

from dhee.core.resolvers import ContextResolver, _classify_preference
from dhee.db.sqlite import FullSQLiteManager


def _fact(subject, predicate, value, *, valid_from=None, confidence=1.0):
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
        confidence=confidence,
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
    path = os.path.join(tmp, "t.db")
    db = FullSQLiteManager(path)
    with db._get_connection() as conn:
        for mid in ("m1", "m2", "m3", "m4"):
            conn.execute(
                "INSERT INTO memories (id, memory, user_id) VALUES (?, ?, ?)",
                (mid, f"mem-{mid}", "u1"),
            )
    return db


class TestSchema:
    def test_tier_columns_present(self, fresh_db):
        with fresh_db._get_connection() as conn:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(engram_facts)").fetchall()
            }
        assert {
            "tier",
            "superseded_by_id",
            "reaffirmed_count",
            "last_reaffirmed_at",
            "schema_v",
        }.issubset(cols)

    def test_preferences_table_exists(self, fresh_db):
        with fresh_db._get_connection() as conn:
            cols = {
                r[1]
                for r in conn.execute(
                    "PRAGMA table_info(engram_preferences)"
                ).fetchall()
            }
        for expected in ("topic", "stance", "tier", "superseded_by_id"):
            assert expected in cols


class TestClassifyPreference:
    @pytest.mark.parametrize(
        "pred,expected_stance",
        [
            ("likes", "positive"),
            ("loves", "positive"),
            ("dislikes", "negative"),
            ("hates", "negative"),
            ("favorite_editor", "positive"),
            ("switched_to", "positive"),
            ("switched_away_from", "negative"),
        ],
    )
    def test_preference_predicates(self, pred, expected_stance):
        result = _classify_preference(pred)
        assert result is not None
        _topic, stance = result
        assert stance == expected_stance

    @pytest.mark.parametrize("pred", ["lives_in", "has_email", "works_at"])
    def test_non_preference_predicates(self, pred):
        assert _classify_preference(pred) is None


class TestSupersede:
    def test_single_valued_predicate_supersedes_old_row(self, fresh_db):
        """Write A, write ¬A on same (subject, predicate): old demoted, new active."""
        r = ContextResolver(fresh_db)
        r.store_engram(
            _engram(_fact("alice", "lives_in", "NYC", valid_from="2024-01-01")),
            "m1",
        )
        r.store_engram(
            _engram(_fact("alice", "lives_in", "SF", valid_from="2025-01-01")),
            "m2",
        )

        with fresh_db._get_connection() as conn:
            rows = {
                r["value"]: dict(r)
                for r in conn.execute(
                    "SELECT value, tier, superseded_by_id, valid_until, id "
                    "FROM engram_facts WHERE predicate='lives_in'"
                )
            }
        assert rows["NYC"]["tier"] == "avoid"
        assert rows["NYC"]["superseded_by_id"] == rows["SF"]["id"]
        assert rows["NYC"]["valid_until"] is not None
        assert rows["SF"]["tier"] == "medium"
        assert rows["SF"]["superseded_by_id"] is None

    def test_superseded_row_retrievable_by_id(self, fresh_db):
        """Demoted rows stay in the table — chain must be explorable."""
        r = ContextResolver(fresh_db)
        r.store_engram(
            _engram(_fact("alice", "lives_in", "NYC", valid_from="2024-01-01")),
            "m1",
        )
        r.store_engram(
            _engram(_fact("alice", "lives_in", "SF", valid_from="2025-01-01")),
            "m2",
        )
        with fresh_db._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM engram_facts WHERE value='NYC'"
            ).fetchone()
        assert row is not None  # not deleted

    def test_resolve_latest_ignores_superseded(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(
            _engram(_fact("alice", "lives_in", "NYC", valid_from="2024-01-01")),
            "m1",
        )
        r.store_engram(
            _engram(_fact("alice", "lives_in", "SF", valid_from="2025-01-01")),
            "m2",
        )
        result = r.resolve_latest("alice", "lives_in", user_id="u1")
        assert result is not None
        assert result["value"] == "SF"


class TestReaffirmation:
    def test_repeat_writes_increment_count_not_insert(self, fresh_db):
        r = ContextResolver(fresh_db)
        fact = _fact("bob", "has_email", "bob@x.com")
        r.store_engram(_engram(fact), "m1")
        r.store_engram(_engram(fact), "m2")
        r.store_engram(_engram(fact), "m3")
        with fresh_db._get_connection() as conn:
            rows = list(
                conn.execute(
                    "SELECT reaffirmed_count, last_reaffirmed_at FROM engram_facts "
                    "WHERE predicate='has_email'"
                )
            )
        assert len(rows) == 1, "reaffirmations must not create duplicate rows"
        assert rows[0]["reaffirmed_count"] == 2  # 2nd and 3rd writes are reaffirms
        assert rows[0]["last_reaffirmed_at"] is not None


class TestPreferenceRouting:
    def test_preference_predicate_lands_in_preferences_store(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("bob", "likes", "pizza")), "m1")
        with fresh_db._get_connection() as conn:
            pref = conn.execute(
                "SELECT subject, topic, stance, value, tier FROM engram_preferences"
            ).fetchone()
        assert pref is not None
        assert dict(pref) == {
            "subject": "bob",
            "topic": "likes",
            "stance": "positive",
            "value": "pizza",
            "tier": "medium",
        }

    def test_preference_supersede_chain(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("bob", "prefers", "vim")), "m1")
        r.store_engram(_engram(_fact("bob", "prefers", "emacs")), "m2")
        with fresh_db._get_connection() as conn:
            rows = {
                r["value"]: dict(r)
                for r in conn.execute(
                    "SELECT value, tier, superseded_by_id, id "
                    "FROM engram_preferences WHERE topic='prefers'"
                )
            }
        assert rows["vim"]["tier"] == "avoid"
        assert rows["vim"]["superseded_by_id"] == rows["emacs"]["id"]
        assert rows["emacs"]["superseded_by_id"] is None

    def test_resolve_latest_prefers_preferences_store(self, fresh_db):
        r = ContextResolver(fresh_db)
        r.store_engram(_engram(_fact("bob", "likes", "pizza")), "m1")
        result = r.resolve_latest("bob", "likes", user_id="u1")
        assert result is not None
        assert result["value"] == "pizza"
        # Shape from preferences store includes stance
        assert result.get("stance") == "positive"
