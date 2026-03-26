"""Tests for engram.core.traces — Benna-Fusi multi-timescale strength model."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from dhee.configs.base import DistillationConfig
from dhee.core.traces import (
    boost_fast_trace,
    cascade_traces,
    compute_effective_strength,
    decay_traces,
    initialize_traces,
)


@pytest.fixture
def config():
    return DistillationConfig(enable_multi_trace=True)


class TestInitializeTraces:
    def test_new_memory_all_in_fast(self):
        s_fast, s_mid, s_slow = initialize_traces(0.8, is_new=True)
        assert s_fast == 0.8
        assert s_mid == 0.0
        assert s_slow == 0.0

    def test_migrated_memory_spread(self):
        s_fast, s_mid, s_slow = initialize_traces(0.6, is_new=False)
        assert s_fast == 0.6
        assert s_mid == pytest.approx(0.3)
        assert s_slow == 0.0

    def test_zero_strength(self):
        s_fast, s_mid, s_slow = initialize_traces(0.0, is_new=True)
        assert s_fast == 0.0
        assert s_mid == 0.0
        assert s_slow == 0.0

    def test_strength_clamped(self):
        s_fast, s_mid, s_slow = initialize_traces(1.5, is_new=True)
        assert s_fast == 1.0
        assert s_mid == 0.0
        assert s_slow == 0.0

    def test_negative_clamped(self):
        s_fast, _, _ = initialize_traces(-0.5, is_new=True)
        assert s_fast == 0.0


class TestComputeEffectiveStrength:
    def test_default_weights(self, config):
        # 0.2*1.0 + 0.3*0.5 + 0.5*0.0 = 0.35
        eff = compute_effective_strength(1.0, 0.5, 0.0, config)
        assert eff == pytest.approx(0.35)

    def test_all_ones(self, config):
        # 0.2*1 + 0.3*1 + 0.5*1 = 1.0
        eff = compute_effective_strength(1.0, 1.0, 1.0, config)
        assert eff == pytest.approx(1.0)

    def test_all_zeros(self, config):
        eff = compute_effective_strength(0.0, 0.0, 0.0, config)
        assert eff == 0.0

    def test_slow_dominates(self, config):
        # 0.2*0 + 0.3*0 + 0.5*0.8 = 0.4
        eff = compute_effective_strength(0.0, 0.0, 0.8, config)
        assert eff == pytest.approx(0.4)

    def test_clamped_to_unit(self, config):
        eff = compute_effective_strength(1.0, 1.0, 1.0, config)
        assert eff <= 1.0


class TestDecayTraces:
    def test_recent_memory_minimal_decay(self, config):
        now = datetime.now(timezone.utc)
        s_f, s_m, s_s = decay_traces(1.0, 0.5, 0.2, now, 0, config)
        assert s_f == pytest.approx(1.0, abs=0.01)
        assert s_m == pytest.approx(0.5, abs=0.01)
        assert s_s == pytest.approx(0.2, abs=0.01)

    def test_old_memory_significant_decay(self, config):
        old = datetime.now(timezone.utc) - timedelta(days=10)
        s_f, s_m, s_s = decay_traces(1.0, 1.0, 1.0, old, 0, config)
        # Fast decays fastest
        assert s_f < s_m < s_s
        assert s_f < 1.0

    def test_access_count_dampens_decay(self, config):
        old = datetime.now(timezone.utc) - timedelta(days=5)
        # No accesses
        f0, m0, s0 = decay_traces(1.0, 1.0, 1.0, old, 0, config)
        # Many accesses
        f10, m10, s10 = decay_traces(1.0, 1.0, 1.0, old, 10, config)
        # More accesses = less decay
        assert f10 > f0
        assert m10 > m0

    def test_string_last_accessed(self, config):
        now = datetime.now(timezone.utc).isoformat()
        s_f, s_m, s_s = decay_traces(1.0, 0.5, 0.2, now, 0, config)
        assert s_f == pytest.approx(1.0, abs=0.01)

    def test_values_clamped(self, config):
        old = datetime.now(timezone.utc) - timedelta(days=100)
        s_f, s_m, s_s = decay_traces(1.0, 1.0, 1.0, old, 0, config)
        assert s_f >= 0.0
        assert s_m >= 0.0
        assert s_s >= 0.0


class TestCascadeTraces:
    def test_normal_cascade_fast_to_mid(self, config):
        s_f, s_m, s_s = cascade_traces(1.0, 0.0, 0.0, config, deep_sleep=False)
        # 10% of fast goes to mid
        assert s_f == pytest.approx(0.9)
        assert s_m == pytest.approx(0.1)
        assert s_s == pytest.approx(0.0)

    def test_deep_sleep_cascade(self, config):
        s_f, s_m, s_s = cascade_traces(1.0, 0.5, 0.0, config, deep_sleep=True)
        # Fast -> mid: fast loses 0.1 (10%), mid gains 0.1 -> mid = 0.6
        # Mid -> slow: mid loses 0.6*0.05 = 0.03, slow gains 0.03
        assert s_f == pytest.approx(0.9)
        assert s_m == pytest.approx(0.57)
        assert s_s == pytest.approx(0.03)

    def test_no_cascade_from_zero(self, config):
        s_f, s_m, s_s = cascade_traces(0.0, 0.0, 0.0, config, deep_sleep=True)
        assert s_f == 0.0
        assert s_m == 0.0
        assert s_s == 0.0

    def test_values_clamped(self, config):
        s_f, s_m, s_s = cascade_traces(1.0, 1.0, 1.0, config, deep_sleep=True)
        assert 0.0 <= s_f <= 1.0
        assert 0.0 <= s_m <= 1.0
        assert 0.0 <= s_s <= 1.0


class TestBoostFastTrace:
    def test_basic_boost(self):
        result = boost_fast_trace(0.5, 0.1)
        assert result == pytest.approx(0.6)

    def test_clamped_at_one(self):
        result = boost_fast_trace(0.95, 0.1)
        assert result == 1.0

    def test_zero_boost(self):
        result = boost_fast_trace(0.5, 0.0)
        assert result == 0.5

    def test_clamped_at_zero(self):
        result = boost_fast_trace(0.0, -0.5)
        assert result == 0.0
