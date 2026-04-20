"""M4.2b regression — MetaBuddhi group-relative confidence.

Plan reference: encapsulated-rolling-bengio.md, Movement 4.2b.

These tests lock in the Dr.RTL-style promotion rule: a candidate strategy
is compared against the parent's per-task-type baseline, not just a single
global average. A catastrophic regression on any one task type blocks
promotion even when the aggregated delta looks positive.
"""

from __future__ import annotations

import pytest

from dhee.core.meta_buddhi import (
    _GROUP_CATASTROPHE_THRESHOLD,
    _MIN_EVAL_COUNT,
    _PROMOTION_THRESHOLD,
    MetaBuddhi,
)
from dhee.core.strategy import StrategyStore


@pytest.fixture()
def mb(tmp_path):
    store = StrategyStore(data_dir=str(tmp_path / "strategies"))
    return MetaBuddhi(data_dir=str(tmp_path / "meta"), strategy_store=store)


# ---------------------------------------------------------------------------
# Baseline accumulation
# ---------------------------------------------------------------------------


class TestGroupBaselines:
    def test_baseline_accumulates_without_pending_attempt(self, mb):
        assert mb.get_stats()["group_baselines"] == {}
        mb.record_evaluation(1.0, task_type="qa")
        mb.record_evaluation(0.0, task_type="qa")
        mb.record_evaluation(1.0, task_type="code")

        baselines = mb.get_stats()["group_baselines"]
        assert baselines["qa"]["n"] == 2
        assert baselines["qa"]["mean"] == pytest.approx(0.5)
        assert baselines["code"]["n"] == 1
        assert baselines["code"]["mean"] == pytest.approx(1.0)

    def test_untagged_calls_do_not_create_groups(self, mb):
        mb.record_evaluation(1.0)
        mb.record_evaluation(0.0)
        assert mb.get_stats()["group_baselines"] == {}

    def test_baselines_persist_across_reload(self, tmp_path):
        store = StrategyStore(data_dir=str(tmp_path / "strategies"))
        mb1 = MetaBuddhi(data_dir=str(tmp_path / "meta"), strategy_store=store)
        mb1.record_evaluation(1.0, task_type="qa")
        mb1.record_evaluation(0.5, task_type="qa")

        mb2 = MetaBuddhi(
            data_dir=str(tmp_path / "meta"),
            strategy_store=StrategyStore(data_dir=str(tmp_path / "strategies")),
        )
        baselines = mb2.get_stats()["group_baselines"]
        assert baselines["qa"]["n"] == 2
        assert baselines["qa"]["mean"] == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Snapshot + resolution
# ---------------------------------------------------------------------------


class TestGroupRelativeResolution:
    def _seed_parent_baseline(self, mb, qa_mean=0.5, code_mean=0.6, n=6):
        """Populate per-task-type baselines on the parent strategy."""
        # Exact sequences so the Welford mean lands on the requested value.
        for _ in range(n):
            mb.record_evaluation(qa_mean, task_type="qa")
            mb.record_evaluation(code_mean, task_type="code")

    def test_attempt_snapshots_parent_baseline_at_propose(self, mb):
        self._seed_parent_baseline(mb)
        attempt = mb.propose_improvement(dimension="semantic_weight")
        assert attempt is not None
        snap = attempt.parent_baseline_by_task
        assert "qa" in snap and "code" in snap
        assert snap["qa"]["mean"] == pytest.approx(0.5)
        assert snap["code"]["mean"] == pytest.approx(0.6)

    def test_group_relative_promote_when_every_group_improves(self, mb):
        self._seed_parent_baseline(mb, qa_mean=0.5, code_mean=0.6)
        attempt = mb.propose_improvement(dimension="semantic_weight")
        assert attempt is not None

        # Candidate: both groups comfortably above parent + threshold.
        for _ in range(3):
            mb.record_evaluation(0.9, task_type="qa")
            mb.record_evaluation(0.9, task_type="code")

        # Force resolution by reaching _MIN_EVAL_COUNT; last call returns status.
        # We fed 6 samples above — already past 5 — so the attempt is done.
        final = mb._attempts[attempt.id]
        assert final.status == "promoted"
        assert final.group_deltas["qa"] == pytest.approx(0.4)
        assert final.group_deltas["code"] == pytest.approx(0.3)

    def test_catastrophic_group_regression_blocks_promotion(self, mb):
        # Easy group (qa) lifts +0.10; hard group (code) tanks
        # by more than _GROUP_CATASTROPHE_THRESHOLD.
        self._seed_parent_baseline(mb, qa_mean=0.5, code_mean=0.8)
        attempt = mb.propose_improvement(dimension="semantic_weight")
        assert attempt is not None

        for _ in range(3):
            mb.record_evaluation(0.60, task_type="qa")    # +0.10 vs parent
            mb.record_evaluation(0.60, task_type="code")  # -0.20 vs parent

        final = mb._attempts[attempt.id]
        assert final.status == "rolled_back"
        # And the catastrophic group delta is surfaced.
        assert final.group_deltas["code"] <= -_GROUP_CATASTROPHE_THRESHOLD

    def test_aggregated_delta_below_threshold_rolls_back(self, mb):
        self._seed_parent_baseline(mb, qa_mean=0.5, code_mean=0.5)
        attempt = mb.propose_improvement(dimension="semantic_weight")
        assert attempt is not None

        # +0.01 on each group — no catastrophe, but below _PROMOTION_THRESHOLD.
        for _ in range(3):
            mb.record_evaluation(0.51, task_type="qa")
            mb.record_evaluation(0.51, task_type="code")

        final = mb._attempts[attempt.id]
        assert final.status == "rolled_back"


class TestUntaggedFallback:
    def test_untagged_scores_use_global_delta(self, mb):
        # Give the parent a global avg of ~0.4 first, THEN propose, THEN
        # feed untagged high scores to the candidate.
        for _ in range(6):
            mb.record_evaluation(0.4)  # no task_type → baseline unchanged
        attempt = mb.propose_improvement(dimension="semantic_weight")
        assert attempt is not None
        # No baselines captured → group path disabled.
        assert attempt.parent_baseline_by_task == {}

        # Candidate scores untagged; _resolve_attempt must fall back to global.
        for _ in range(_MIN_EVAL_COUNT):
            mb.record_evaluation(0.9)

        final = mb._attempts[attempt.id]
        # With parent having no recorded eval_scores on the strategy, the
        # fallback baseline is 0.5; candidate avg 0.9 → delta 0.4 → promote.
        assert final.status == "promoted"
        assert final.group_deltas == {}
