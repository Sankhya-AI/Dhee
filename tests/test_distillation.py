"""Tests for engram.core.distillation — Replay-driven semantic distillation."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from dhee.configs.base import DistillationConfig
from dhee.core.distillation import ReplayDistiller
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
def config():
    return DistillationConfig(
        enable_distillation=True,
        distillation_batch_size=10,
        distillation_min_episodes=2,
        max_semantic_per_batch=3,
    )


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.generate.return_value = json.dumps({
        "semantic_facts": [
            {
                "content": "User prefers TypeScript for frontend development",
                "importance": "high",
                "source_episodes": ["ep1", "ep2"],
                "reasoning": "Mentioned multiple times",
            },
            {
                "content": "User deploys on Fridays using CI/CD pipeline",
                "importance": "medium",
                "source_episodes": ["ep3"],
                "reasoning": "Consistent pattern",
            },
        ],
        "skipped_as_temporary": ["one-time error discussion"],
    })
    return llm


def _add_episodic_memory(db, user_id, content, created_at=None):
    """Helper to add an episodic memory directly to the DB."""
    now = created_at or datetime.now(timezone.utc).isoformat()
    db.add_memory({
        "memory": content,
        "user_id": user_id,
        "memory_type": "episodic",
        "created_at": now,
        "updated_at": now,
        "layer": "sml",
        "strength": 1.0,
    })


class TestReplayDistiller:
    def test_disabled_returns_skipped(self, tmp_db, mock_llm):
        config = DistillationConfig(enable_distillation=False)
        distiller = ReplayDistiller(tmp_db, mock_llm, config)
        result = distiller.run("user1")
        assert result["skipped"] is True
        assert result["reason"] == "distillation disabled"

    def test_insufficient_episodes(self, tmp_db, mock_llm, config):
        # Add only 1 episode (min is 2)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        _add_episodic_memory(tmp_db, "user1", "Hello", f"{yesterday}T12:00:00")

        distiller = ReplayDistiller(tmp_db, mock_llm, config)
        result = distiller.run("user1", date_str=yesterday)
        assert result["skipped"] is True
        assert result["reason"] == "insufficient episodes"

    def test_successful_distillation(self, tmp_db, mock_llm, config):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        for i in range(5):
            _add_episodic_memory(
                tmp_db, "user1", f"Episode content {i}",
                f"{yesterday}T{10+i}:00:00",
            )

        add_fn = MagicMock()
        add_fn.return_value = {
            "results": [{"id": f"sem_{i}", "event": "ADD"}]
        }

        distiller = ReplayDistiller(tmp_db, mock_llm, config)
        result = distiller.run("user1", date_str=yesterday, memory_add_fn=add_fn)

        assert result.get("skipped") is not True
        assert result["episodes_sampled"] == 5
        assert result["semantic_created"] == 2
        assert "run_id" in result
        # LLM was called
        assert mock_llm.generate.called

    def test_dedup_detection(self, tmp_db, mock_llm, config):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        for i in range(3):
            _add_episodic_memory(
                tmp_db, "user1", f"Episode {i}",
                f"{yesterday}T{10+i}:00:00",
            )

        add_fn = MagicMock()
        # First fact gets added, second gets deduplicated
        add_fn.side_effect = [
            {"results": [{"id": "sem_1", "event": "ADD"}]},
            {"results": [{"id": "existing", "event": "NOOP"}]},
        ]

        distiller = ReplayDistiller(tmp_db, mock_llm, config)
        result = distiller.run("user1", date_str=yesterday, memory_add_fn=add_fn)

        assert result["semantic_created"] == 1
        assert result["semantic_deduplicated"] == 1

    def test_invalid_llm_response(self, tmp_db, config):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        for i in range(3):
            _add_episodic_memory(
                tmp_db, "user1", f"Episode {i}",
                f"{yesterday}T{10+i}:00:00",
            )

        bad_llm = MagicMock()
        bad_llm.generate.return_value = "not valid json at all"

        distiller = ReplayDistiller(tmp_db, bad_llm, config)
        result = distiller.run("user1", date_str=yesterday, memory_add_fn=MagicMock())

        # Should not crash, just produce 0 facts
        assert result.get("semantic_created", 0) == 0


class TestDistillationProvenance:
    def test_provenance_recorded(self, tmp_db, mock_llm, config):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        for i in range(3):
            _add_episodic_memory(
                tmp_db, "user1", f"Episode {i}",
                f"{yesterday}T{10+i}:00:00",
            )

        add_fn = MagicMock()
        add_fn.return_value = {"results": [{"id": "sem_123", "event": "ADD"}]}

        distiller = ReplayDistiller(tmp_db, mock_llm, config)
        distiller.run("user1", date_str=yesterday, memory_add_fn=add_fn)

        # Check provenance was recorded
        with tmp_db._get_connection() as conn:
            rows = conn.execute("SELECT * FROM distillation_provenance").fetchall()
        assert len(rows) > 0


class TestDistillationLog:
    def test_log_recorded(self, tmp_db, mock_llm, config):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        for i in range(3):
            _add_episodic_memory(
                tmp_db, "user1", f"Episode {i}",
                f"{yesterday}T{10+i}:00:00",
            )

        add_fn = MagicMock()
        add_fn.return_value = {"results": [{"id": "sem_1", "event": "ADD"}]}

        distiller = ReplayDistiller(tmp_db, mock_llm, config)
        result = distiller.run("user1", date_str=yesterday, memory_add_fn=add_fn)

        with tmp_db._get_connection() as conn:
            rows = conn.execute("SELECT * FROM distillation_log WHERE user_id = 'user1'").fetchall()
        assert len(rows) == 1
        assert rows[0]["episodes_sampled"] == 3
