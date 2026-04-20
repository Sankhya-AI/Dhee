"""M7.8 regression — decades longevity scorecard.

Plan reference: encapsulated-rolling-bengio.md, Movement 7.8 /
Movement 3 verification line.

The live 10K run publishes the headline numbers (see
``dhee.doctor`` + README). These tests exercise a fast small-scale
corpus so future changes can't silently claim longevity they don't
deliver.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dhee.benchmarks.decades import (
    CANONICAL_RETENTION_REQUIRED,
    DecadesConfig,
    DecadesScorecard,
    MAX_LATENCY_DEGRADATION,
    SUPERSEDE_INTEGRITY_REQUIRED,
    run_decades_eval,
)


# ---------------------------------------------------------------------------
# Scorecard dataclass contract
# ---------------------------------------------------------------------------


class TestScorecardContract:
    def test_dataclass_round_trip(self):
        cfg = DecadesConfig(total_events=10)
        s = DecadesScorecard(config=cfg)
        d = s.to_dict()
        assert d["config"]["total_events"] == 10
        assert d["passed"] is False
        assert d["canonical_retention"] == 0.0


# ---------------------------------------------------------------------------
# End-to-end: all three invariants must hold on a small corpus
# ---------------------------------------------------------------------------


class TestSmallCorpus:
    @pytest.fixture(scope="class")
    def scorecard(self, tmp_path_factory) -> DecadesScorecard:
        work = tmp_path_factory.mktemp("decades")
        cfg = DecadesConfig(
            total_events=400,
            supersede_fraction=0.20,
            canonical_fraction=0.10,
            latency_samples=30,
            seed=7,
        )
        return run_decades_eval(data_dir=str(work), config=cfg)

    def test_canonical_rows_retained_at_full(self, scorecard):
        # The consolidator's sweep must leave every canonical row
        # untouched. This is the 'write-once, evict-never' invariant.
        assert scorecard.canonical_rows > 0
        assert (
            scorecard.canonical_retained_after_sweep
            == scorecard.canonical_rows
        )
        assert scorecard.canonical_retention == 1.0
        assert scorecard.canonical_retention >= CANONICAL_RETENTION_REQUIRED

    def test_supersede_chains_stay_explorable(self, scorecard):
        assert scorecard.supersede_chains > 0
        assert (
            scorecard.supersede_chain_integrity
            >= SUPERSEDE_INTEGRITY_REQUIRED
        )
        # At this scale, every old row is old enough for the sweep —
        # they should all have moved into engram_fact_archive.
        assert scorecard.archived_old_rows == scorecard.supersede_chains

    def test_latency_degradation_within_budget(self, scorecard):
        assert scorecard.latency_10k_p50_ms >= 0
        assert scorecard.latency_1k_p50_ms >= 0
        assert scorecard.latency_degradation <= MAX_LATENCY_DEGRADATION

    def test_passed_overall(self, scorecard):
        assert scorecard.passed is True
        assert scorecard.notes == []


# ---------------------------------------------------------------------------
# Failure paths — make sure the scorecard doesn't silently pass
# ---------------------------------------------------------------------------


class TestFailureModes:
    def test_impossible_latency_budget_flips_passed_false(
        self, tmp_path, monkeypatch,
    ):
        # Force the latency invariant to trip while the other two still
        # hold. Patch the module constant to a value 1.0 cannot clear.
        import dhee.benchmarks.decades as dec

        monkeypatch.setattr(dec, "MAX_LATENCY_DEGRADATION", -1.0)
        cfg = DecadesConfig(total_events=300, latency_samples=20, seed=11)
        s = dec.run_decades_eval(data_dir=str(tmp_path), config=cfg)
        assert s.canonical_retention == 1.0
        assert s.supersede_chain_integrity == 1.0
        assert s.passed is False
        assert any("latency_degradation" in n for n in s.notes)

    def test_counts_are_honest_numbers(self, tmp_path):
        cfg = DecadesConfig(total_events=200, latency_samples=20, seed=19)
        s = run_decades_eval(data_dir=str(tmp_path), config=cfg)
        # total_facts_written counts what actually landed in engram_facts
        # (not what the config requested), so refactors that quietly
        # drop rows get caught here.
        assert s.total_facts_written == 200

    def test_data_dir_reused(self, tmp_path):
        cfg = DecadesConfig(total_events=150, latency_samples=20)
        s = run_decades_eval(data_dir=str(tmp_path), config=cfg)
        # The eval writes its DBs under the caller's data_dir — the
        # main/ subdir must exist after the run so users can inspect
        # the generated state.
        assert (Path(tmp_path) / "main" / "decades.db").exists()
        assert (Path(tmp_path) / "ref1k" / "decades.db").exists()
        assert s.passed is True
