"""Tests for engram-enterprise package — schema extension and basic imports."""

import os
import sqlite3
import tempfile

import pytest

from engram_enterprise.schema import extend_schema, ENTERPRISE_MIGRATIONS


class TestSchemaExtension:
    def test_extend_creates_tables(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        # Create a minimal memories table (simulating engram-memory's DB)
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                memory TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

        # Extend with enterprise schema
        extend_schema(db_path)

        # Verify governance tables exist
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected = {
            "views",
            "proposal_commits",
            "proposal_changes",
            "conflict_stash",
            "sessions",
            "memory_refcounts",
            "memory_subscribers",
            "daily_digests",
            "invariants",
            "agent_trust",
            "namespaces",
            "namespace_permissions",
            "agent_policies",
        }
        assert expected.issubset(tables), f"Missing: {expected - tables}"

    def test_extend_idempotent(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        extend_schema(db_path)
        extend_schema(db_path)  # Should not raise

    def test_migrations_tracked(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        extend_schema(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [row[0] for row in cursor.fetchall()]
        conn.close()

        for key in ENTERPRISE_MIGRATIONS:
            assert key in versions, f"Migration {key} not tracked"


class TestEnterpriseImports:
    def test_schema_importable(self):
        from engram_enterprise.schema import extend_schema
        assert callable(extend_schema)

    def test_policy_importable(self):
        from engram_enterprise.policy import feature_enabled
        assert callable(feature_enabled)

    def test_invariants_importable(self):
        from engram_enterprise.invariants import InvariantEngine
        assert InvariantEngine is not None

    def test_acceptance_importable(self):
        from engram_enterprise.acceptance import detect_explicit_intent
        assert callable(detect_explicit_intent)

    def test_provenance_importable(self):
        from engram_enterprise.provenance import build_provenance
        assert callable(build_provenance)

    def test_refcounts_importable(self):
        from engram_enterprise.refcounts import RefCountManager
        assert RefCountManager is not None

    def test_context_packer_importable(self):
        from engram_enterprise.context_packer import pack_context
        assert callable(pack_context)

    def test_reranker_importable(self):
        from engram_enterprise.reranker import intersection_promote
        assert callable(intersection_promote)
