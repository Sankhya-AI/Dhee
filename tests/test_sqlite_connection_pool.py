"""Tests for SQLite connection pooling, batch ops, and type safety (Phases 1, 2, 5)."""

import os
import tempfile
import threading

import pytest

from dhee.db.sqlite import SQLiteManager, VALID_MEMORY_COLUMNS, VALID_SCENE_COLUMNS, _utcnow_iso


@pytest.fixture
def db_manager():
    """Create a temporary SQLiteManager for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = SQLiteManager(path)
    yield mgr
    mgr.close()
    os.unlink(path)


def _add_test_memory(mgr, memory_id="test-1", content="Hello world", user_id="user1"):
    now = _utcnow_iso()
    mgr.add_memory({
        "id": memory_id,
        "memory": content,
        "user_id": user_id,
        "created_at": now,
        "updated_at": now,
        "layer": "sml",
        "strength": 1.0,
    })
    return memory_id


class TestConnectionPool:
    def test_persistent_connection_wal_mode(self, db_manager):
        """Verify WAL mode is enabled on the persistent connection."""
        with db_manager._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"

    def test_connection_reuse(self, db_manager):
        """Same connection object is yielded on successive calls."""
        with db_manager._get_connection() as conn1:
            pass
        with db_manager._get_connection() as conn2:
            pass
        assert conn1 is conn2

    def test_thread_safety(self, db_manager):
        """Concurrent threads can safely access the DB."""
        _add_test_memory(db_manager, "thread-1")
        results = []

        def reader():
            mem = db_manager.get_memory("thread-1")
            results.append(mem is not None)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(results)

    def test_close(self, db_manager):
        """close() shuts down cleanly."""
        _add_test_memory(db_manager, "close-test")
        db_manager.close()
        # Connection is None after close.
        assert db_manager._conn is None

    def test_repr(self, db_manager):
        assert "SQLiteManager" in repr(db_manager)
        assert db_manager.db_path in repr(db_manager)


class TestBatchOperations:
    def test_get_memories_bulk(self, db_manager):
        _add_test_memory(db_manager, "bulk-1", "Memory 1")
        _add_test_memory(db_manager, "bulk-2", "Memory 2")
        _add_test_memory(db_manager, "bulk-3", "Memory 3")

        result = db_manager.get_memories_bulk(["bulk-1", "bulk-3"])
        assert len(result) == 2
        assert "bulk-1" in result
        assert "bulk-3" in result
        assert result["bulk-1"]["memory"] == "Memory 1"

    def test_get_memories_bulk_empty(self, db_manager):
        assert db_manager.get_memories_bulk([]) == {}

    def test_get_memories_bulk_missing(self, db_manager):
        result = db_manager.get_memories_bulk(["nonexistent"])
        assert len(result) == 0

    def test_increment_access_bulk(self, db_manager):
        _add_test_memory(db_manager, "inc-1")
        _add_test_memory(db_manager, "inc-2")

        db_manager.increment_access_bulk(["inc-1", "inc-2"])

        mem1 = db_manager.get_memory("inc-1")
        mem2 = db_manager.get_memory("inc-2")
        assert mem1["access_count"] == 1
        assert mem2["access_count"] == 1

    def test_increment_access_bulk_empty(self, db_manager):
        db_manager.increment_access_bulk([])  # Should not raise.

    def test_update_strength_bulk(self, db_manager):
        _add_test_memory(db_manager, "str-1")
        _add_test_memory(db_manager, "str-2")

        db_manager.update_strength_bulk({"str-1": 0.8, "str-2": 0.6})

        mem1 = db_manager.get_memory("str-1")
        mem2 = db_manager.get_memory("str-2")
        assert abs(mem1["strength"] - 0.8) < 0.01
        assert abs(mem2["strength"] - 0.6) < 0.01


class TestTypeSafety:
    def test_update_memory_rejects_invalid_column(self, db_manager):
        _add_test_memory(db_manager, "safe-1")
        with pytest.raises(ValueError, match="Invalid memory column"):
            db_manager.update_memory("safe-1", {"robert_tables; DROP TABLE memories--": "hacked"})

    def test_update_memory_valid_columns(self, db_manager):
        _add_test_memory(db_manager, "safe-2")
        assert db_manager.update_memory("safe-2", {"strength": 0.5})
        mem = db_manager.get_memory("safe-2")
        assert abs(mem["strength"] - 0.5) < 0.01

    def test_update_scene_rejects_invalid_column(self, db_manager):
        scene_id = db_manager.add_scene({"id": "scene-1", "user_id": "u1", "start_time": _utcnow_iso()})
        with pytest.raises(ValueError, match="Invalid scene column"):
            db_manager.update_scene(scene_id, {"evil_column": "hack"})

    def test_update_profile_rejects_invalid_column(self, db_manager):
        pid = db_manager.add_profile({"id": "prof-1", "user_id": "u1", "name": "Test"})
        with pytest.raises(ValueError, match="Invalid profile column"):
            db_manager.update_profile(pid, {"evil_column": "hack"})

    def test_migrate_add_column_rejects_invalid_table(self, db_manager):
        with db_manager._get_connection() as conn:
            with pytest.raises(ValueError, match="Invalid table"):
                db_manager._migrate_add_column_conn(conn, "evil_table", "col", "TEXT")

    def test_migrate_add_column_rejects_invalid_column_name(self, db_manager):
        with db_manager._get_connection() as conn:
            with pytest.raises(ValueError, match="Invalid column name"):
                db_manager._migrate_add_column_conn(conn, "memories", "evil;drop", "TEXT")

    def test_valid_columns_frozensets_not_empty(self):
        assert len(VALID_MEMORY_COLUMNS) > 10
        assert len(VALID_SCENE_COLUMNS) > 5


class TestMigrationIdempotency:
    def test_v2_columns_complete_marker(self, db_manager):
        """After init, the v2_columns_complete migration should be applied."""
        with db_manager._get_connection() as conn:
            assert db_manager._is_migration_applied(conn, "v2_columns_complete")

    def test_reinit_skips_backfills(self, db_manager):
        """Re-running _init_db should be fast because migrations are skipped."""
        # Just verify it doesn't error on second run.
        db_manager._init_db()
        with db_manager._get_connection() as conn:
            assert db_manager._is_migration_applied(conn, "v2_columns_complete")
