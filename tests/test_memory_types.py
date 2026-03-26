"""Tests for CLS memory type classification and backward compatibility."""

import os
import tempfile

import pytest

from dhee.configs.base import DistillationConfig, MemoryConfig
from dhee.core.intent import QueryIntent, classify_intent
from dhee.core.traces import compute_effective_strength, initialize_traces
from dhee.db.sqlite import SQLiteManager


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SQLiteManager(path)
    yield db
    db.close()
    os.unlink(path)


class TestDistillationConfig:
    def test_defaults_all_enabled(self):
        config = DistillationConfig()
        assert config.enable_memory_types is True
        assert config.enable_distillation is True
        assert config.enable_interference_pruning is True
        assert config.enable_redundancy_collapse is True
        assert config.enable_homeostasis is True
        assert config.enable_multi_trace is True
        assert config.enable_intent_routing is True

    def test_memory_config_has_distillation(self):
        mc = MemoryConfig()
        assert hasattr(mc, "distillation")
        assert isinstance(mc.distillation, DistillationConfig)

    def test_version_updated(self):
        mc = MemoryConfig()
        assert mc.version == "v1.4"

    def test_default_config_has_cls_enabled(self):
        """Verify a default MemoryConfig has CLS features enabled."""
        mc = MemoryConfig()
        assert mc.distillation.enable_memory_types is True
        assert mc.distillation.default_memory_type == "semantic"

    def test_custom_config(self):
        config = DistillationConfig(
            enable_memory_types=True,
            enable_multi_trace=True,
            enable_intent_routing=True,
        )
        assert config.enable_memory_types is True
        assert config.enable_multi_trace is True
        assert config.enable_intent_routing is True


class TestDBMemoryTypeColumn:
    def test_memory_type_column_exists(self, tmp_db):
        """Verify memory_type column was added by migration."""
        with tmp_db._get_connection() as conn:
            row = conn.execute(
                "PRAGMA table_info(memories)"
            ).fetchall()
        columns = {r["name"] for r in row}
        assert "memory_type" in columns
        assert "s_fast" in columns
        assert "s_mid" in columns
        assert "s_slow" in columns

    def test_add_memory_with_type(self, tmp_db):
        mid = tmp_db.add_memory({
            "memory": "Test episodic memory",
            "user_id": "user1",
            "memory_type": "episodic",
        })
        mem = tmp_db.get_memory(mid)
        assert mem["memory_type"] == "episodic"

    def test_add_memory_default_type(self, tmp_db):
        mid = tmp_db.add_memory({
            "memory": "Test default memory",
            "user_id": "user1",
        })
        mem = tmp_db.get_memory(mid)
        assert mem["memory_type"] == "semantic"

    def test_add_memory_with_traces(self, tmp_db):
        mid = tmp_db.add_memory({
            "memory": "Traced memory",
            "user_id": "user1",
            "s_fast": 0.8,
            "s_mid": 0.0,
            "s_slow": 0.0,
        })
        mem = tmp_db.get_memory(mid)
        assert mem["s_fast"] == 0.8
        assert mem["s_mid"] == 0.0
        assert mem["s_slow"] == 0.0

    def test_update_multi_trace(self, tmp_db):
        mid = tmp_db.add_memory({
            "memory": "Trace update test",
            "user_id": "user1",
            "strength": 1.0,
            "s_fast": 1.0,
            "s_mid": 0.0,
            "s_slow": 0.0,
        })
        tmp_db.update_multi_trace(mid, 0.5, 0.3, 0.1, 0.35)
        mem = tmp_db.get_memory(mid)
        assert mem["s_fast"] == 0.5
        assert mem["s_mid"] == 0.3
        assert mem["s_slow"] == 0.1
        assert mem["strength"] == pytest.approx(0.35)


class TestDBEpisodicMemories:
    def test_get_episodic_memories(self, tmp_db):
        tmp_db.add_memory({
            "memory": "Episodic 1",
            "user_id": "user1",
            "memory_type": "episodic",
        })
        tmp_db.add_memory({
            "memory": "Semantic 1",
            "user_id": "user1",
            "memory_type": "semantic",
        })
        eps = tmp_db.get_episodic_memories("user1")
        assert len(eps) == 1
        assert eps[0]["memory"] == "Episodic 1"

    def test_get_episodic_empty(self, tmp_db):
        eps = tmp_db.get_episodic_memories("nonexistent")
        assert eps == []


class TestDBDistillationTables:
    def test_distillation_log(self, tmp_db):
        run_id = tmp_db.log_distillation_run(
            "user1",
            episodes_sampled=10,
            semantic_created=2,
            semantic_deduplicated=1,
        )
        assert run_id  # non-empty string

    def test_distillation_provenance(self, tmp_db):
        tmp_db.add_distillation_provenance(
            semantic_memory_id="sem_1",
            episodic_memory_ids=["ep_1", "ep_2"],
            run_id="run_1",
        )
        with tmp_db._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM distillation_provenance WHERE semantic_memory_id = 'sem_1'"
            ).fetchall()
        assert len(rows) == 2

    def test_memory_count_by_namespace(self, tmp_db):
        for i in range(3):
            tmp_db.add_memory({
                "memory": f"Mem {i}",
                "user_id": "user1",
                "namespace": "default",
            })
        tmp_db.add_memory({
            "memory": "Work mem",
            "user_id": "user1",
            "namespace": "work",
        })
        counts = tmp_db.get_memory_count_by_namespace("user1")
        assert counts.get("default", 0) == 3
        assert counts.get("work", 0) == 1


class TestMultiTraceIntegration:
    def test_trace_lifecycle(self):
        """Test the full lifecycle: initialize -> compute -> cascade."""
        config = DistillationConfig(enable_multi_trace=True)

        # New memory: all in fast
        s_f, s_m, s_s = initialize_traces(0.9, is_new=True)
        assert s_f == 0.9
        assert s_m == 0.0
        assert s_s == 0.0

        eff = compute_effective_strength(s_f, s_m, s_s, config)
        assert eff == pytest.approx(0.2 * 0.9)  # Only fast has value

        # After deep sleep cascade
        from dhee.core.traces import cascade_traces
        s_f2, s_m2, s_s2 = cascade_traces(s_f, s_m, s_s, config, deep_sleep=True)
        assert s_m2 > 0.0  # Some transferred to mid
        eff2 = compute_effective_strength(s_f2, s_m2, s_s2, config)
        # Total energy should shift across traces but weighted sum may differ
        assert eff2 > 0.0
