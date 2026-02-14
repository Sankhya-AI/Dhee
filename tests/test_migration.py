"""Tests for schema migration idempotency."""

import os
import sqlite3
import tempfile

import pytest

from engram.db.sqlite import SQLiteManager


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


class TestMigrationIdempotency:
    def test_double_init(self, db_path):
        """Tables should be created with IF NOT EXISTS — double init is safe."""
        mgr1 = SQLiteManager(db_path)
        mgr2 = SQLiteManager(db_path)  # Should not raise

        # Both should work
        scenes = mgr2.get_scenes(user_id="test")
        assert scenes == []

    def test_tables_exist(self, db_path):
        """All expected tables should be created."""
        mgr = SQLiteManager(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected = {
            "memories",
            "memory_history",
            "decay_log",
            "categories",
            "scenes",
            "scene_memories",
            "profiles",
            "profile_memories",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_scene_id_column_migration(self, db_path):
        """scene_id column should be added to memories table."""
        mgr = SQLiteManager(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(memories)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "scene_id" in columns

    def test_existing_data_untouched(self, db_path):
        """Adding new tables should not affect existing data."""
        # Create with old schema (just memories)
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                memory TEXT NOT NULL,
                user_id TEXT,
                agent_id TEXT,
                run_id TEXT,
                app_id TEXT,
                metadata TEXT DEFAULT '{}',
                categories TEXT DEFAULT '[]',
                immutable INTEGER DEFAULT 0,
                expiration_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                layer TEXT DEFAULT 'sml',
                strength REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                last_accessed TEXT DEFAULT CURRENT_TIMESTAMP,
                embedding TEXT,
                related_memories TEXT DEFAULT '[]',
                source_memories TEXT DEFAULT '[]',
                tombstone INTEGER DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO memories (id, memory, user_id) VALUES ('test1', 'hello world', 'u1')"
        )
        conn.commit()
        conn.close()

        # Now init with SQLiteManager (should add new tables without touching existing data)
        mgr = SQLiteManager(db_path)
        mem = mgr.get_memory("test1")
        assert mem is not None
        assert mem["memory"] == "hello world"

    def test_distillation_tables_exist(self, db_path):
        """Distillation tables should be created."""
        SQLiteManager(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "distillation_provenance" in tables
        assert "distillation_log" in tables
