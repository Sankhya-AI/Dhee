"""Tests for engram.core.forgetting — Advanced forgetting mechanisms."""

import os
import tempfile
from unittest.mock import MagicMock, PropertyMock

import pytest

from dhee.configs.base import DistillationConfig, FadeMemConfig
from dhee.core.forgetting import (
    HomeostaticNormalizer,
    InterferencePruner,
    RedundancyCollapser,
)
from dhee.db.sqlite import SQLiteManager


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SQLiteManager(path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def fadem_config():
    return FadeMemConfig(
        conflict_similarity_threshold=0.85,
        forgetting_threshold=0.1,
    )


def _make_memory(mid, content, strength=0.5, embedding=None, immutable=False):
    return {
        "id": mid,
        "memory": content,
        "strength": strength,
        "embedding": embedding or [0.1, 0.2],
        "immutable": immutable,
    }


class TestInterferencePruner:
    def test_disabled(self, tmp_db, fadem_config):
        config = DistillationConfig(enable_interference_pruning=False)
        pruner = InterferencePruner(tmp_db, config, fadem_config)
        result = pruner.run([_make_memory("m1", "test")])
        assert result == {"checked": 0, "demoted": 0}

    def test_no_search_fn(self, tmp_db, fadem_config):
        config = DistillationConfig(enable_interference_pruning=True)
        pruner = InterferencePruner(tmp_db, config, fadem_config)
        result = pruner.run([_make_memory("m1", "test")])
        assert result == {"checked": 0, "demoted": 0}

    def test_skips_immutable(self, tmp_db, fadem_config):
        config = DistillationConfig(enable_interference_pruning=True)
        mock_search = MagicMock(return_value=[])
        mock_resolve = MagicMock()
        pruner = InterferencePruner(
            tmp_db, config, fadem_config,
            resolve_conflict_fn=mock_resolve,
            search_fn=mock_search,
        )
        memories = [_make_memory("m1", "test", immutable=True)]
        result = pruner.run(memories)
        assert result["checked"] == 0

    def test_skips_low_strength(self, tmp_db, fadem_config):
        config = DistillationConfig(enable_interference_pruning=True)
        mock_search = MagicMock(return_value=[])
        mock_resolve = MagicMock()
        pruner = InterferencePruner(
            tmp_db, config, fadem_config,
            resolve_conflict_fn=mock_resolve,
            search_fn=mock_search,
        )
        memories = [_make_memory("m1", "test", strength=0.1)]
        result = pruner.run(memories)
        assert result["checked"] == 0


class TestRedundancyCollapser:
    def test_disabled(self, tmp_db):
        config = DistillationConfig(enable_redundancy_collapse=False)
        collapser = RedundancyCollapser(tmp_db, config)
        result = collapser.run([_make_memory("m1", "test")])
        assert result == {"groups_fused": 0, "memories_fused": 0}

    def test_no_fuse_fn(self, tmp_db):
        config = DistillationConfig(enable_redundancy_collapse=True)
        collapser = RedundancyCollapser(tmp_db, config)
        result = collapser.run([_make_memory("m1", "test")])
        assert result == {"groups_fused": 0, "memories_fused": 0}

    def test_skips_immutable(self, tmp_db):
        config = DistillationConfig(enable_redundancy_collapse=True)
        mock_search = MagicMock(return_value=[])
        mock_fuse = MagicMock()
        collapser = RedundancyCollapser(tmp_db, config, fuse_fn=mock_fuse, search_fn=mock_search)
        memories = [_make_memory("m1", "test", immutable=True)]
        result = collapser.run(memories)
        assert result["groups_fused"] == 0


class TestHomeostaticNormalizer:
    def test_disabled(self, tmp_db, fadem_config):
        config = DistillationConfig(enable_homeostasis=False)
        normalizer = HomeostaticNormalizer(tmp_db, config, fadem_config)
        result = normalizer.run("user1")
        assert result == {"namespaces_over_budget": 0, "pressured": 0, "forgotten": 0}

    def test_under_budget_no_action(self, tmp_db, fadem_config):
        config = DistillationConfig(
            enable_homeostasis=True,
            homeostasis_budget_per_namespace=5000,
        )
        # Add a few memories — well under budget
        for i in range(3):
            tmp_db.add_memory({
                "memory": f"Memory {i}",
                "user_id": "user1",
                "namespace": "default",
                "strength": 0.5,
            })

        normalizer = HomeostaticNormalizer(tmp_db, config, fadem_config)
        result = normalizer.run("user1")
        assert result["namespaces_over_budget"] == 0

    def test_over_budget_applies_pressure(self, tmp_db, fadem_config):
        config = DistillationConfig(
            enable_homeostasis=True,
            homeostasis_budget_per_namespace=5,  # Very low budget
            homeostasis_pressure_factor=0.5,
        )
        # Add 10 memories, which exceeds budget of 5
        for i in range(10):
            tmp_db.add_memory({
                "memory": f"Memory {i}",
                "user_id": "user1",
                "namespace": "default",
                "strength": 0.3,
            })

        mock_delete = MagicMock()
        normalizer = HomeostaticNormalizer(tmp_db, config, fadem_config, delete_fn=mock_delete)
        result = normalizer.run("user1")
        assert result["namespaces_over_budget"] == 1
        # Some memories should have been pressured or deleted
        assert result["pressured"] + result["forgotten"] > 0
