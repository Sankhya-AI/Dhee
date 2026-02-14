"""Enterprise DB schema extension.

Adds governance tables to engram-memory's SQLite database at runtime.
Call `extend_schema(db_path)` once at startup to ensure all enterprise
tables exist alongside the core memory tables.
"""

import sqlite3
import threading
from typing import Optional


_lock = threading.Lock()

# Enterprise governance tables — keyed by migration version
ENTERPRISE_MIGRATIONS = {
    "ent_001": """
        CREATE TABLE IF NOT EXISTS views (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT,
            timestamp TEXT NOT NULL,
            place_type TEXT,
            place_value TEXT,
            topic_label TEXT,
            topic_embedding_ref TEXT,
            characters TEXT DEFAULT '[]',
            raw_text TEXT,
            signals TEXT DEFAULT '{}',
            scene_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_views_user_time ON views(user_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_views_scene ON views(scene_id);
    """,
    "ent_002": """
        CREATE TABLE IF NOT EXISTS proposal_commits (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT,
            scope TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            checks TEXT DEFAULT '{}',
            preview TEXT DEFAULT '{}',
            provenance TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_proposal_commits_user ON proposal_commits(user_id);
        CREATE INDEX IF NOT EXISTS idx_proposal_commits_status ON proposal_commits(status);

        CREATE TABLE IF NOT EXISTS proposal_changes (
            id TEXT PRIMARY KEY,
            commit_id TEXT NOT NULL,
            op TEXT NOT NULL,
            target TEXT NOT NULL,
            target_id TEXT,
            patch TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (commit_id) REFERENCES proposal_commits(id)
        );
        CREATE INDEX IF NOT EXISTS idx_proposal_changes_commit ON proposal_changes(commit_id);
    """,
    "ent_003": """
        CREATE TABLE IF NOT EXISTS conflict_stash (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            conflict_key TEXT NOT NULL,
            existing TEXT DEFAULT '{}',
            proposed TEXT DEFAULT '{}',
            resolution TEXT NOT NULL DEFAULT 'UNRESOLVED',
            source_commit_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_conflict_stash_user ON conflict_stash(user_id);
        CREATE INDEX IF NOT EXISTS idx_conflict_stash_resolution ON conflict_stash(resolution);
    """,
    "ent_004": """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            agent_id TEXT,
            allowed_confidentiality_scopes TEXT DEFAULT '[]',
            capabilities TEXT DEFAULT '[]',
            expires_at TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            revoked_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
    """,
    "ent_005": """
        CREATE TABLE IF NOT EXISTS memory_refcounts (
            memory_id TEXT PRIMARY KEY,
            strong_count INTEGER DEFAULT 0,
            weak_count INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (memory_id) REFERENCES memories(id)
        );

        CREATE TABLE IF NOT EXISTS memory_subscribers (
            id TEXT PRIMARY KEY,
            memory_id TEXT NOT NULL,
            subscriber TEXT NOT NULL,
            ref_type TEXT NOT NULL CHECK(ref_type IN ('strong','weak')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(memory_id, subscriber, ref_type),
            FOREIGN KEY (memory_id) REFERENCES memories(id)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_subscribers_memory ON memory_subscribers(memory_id);
    """,
    "ent_006": """
        CREATE TABLE IF NOT EXISTS daily_digests (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            digest_date TEXT NOT NULL,
            payload TEXT DEFAULT '{}',
            generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, digest_date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_digests_user_date ON daily_digests(user_id, digest_date);
    """,
    "ent_007": """
        CREATE TABLE IF NOT EXISTS invariants (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            invariant_key TEXT NOT NULL,
            invariant_value TEXT NOT NULL,
            category TEXT DEFAULT 'identity',
            confidence REAL DEFAULT 0.0,
            source_memory_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, invariant_key)
        );
        CREATE INDEX IF NOT EXISTS idx_invariants_user ON invariants(user_id);
    """,
    "ent_008": """
        CREATE TABLE IF NOT EXISTS agent_trust (
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            total_proposals INTEGER DEFAULT 0,
            approved_proposals INTEGER DEFAULT 0,
            rejected_proposals INTEGER DEFAULT 0,
            auto_stashed_proposals INTEGER DEFAULT 0,
            last_proposed_at TEXT,
            last_approved_at TEXT,
            trust_score REAL DEFAULT 0.0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, agent_id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_trust_user ON agent_trust(user_id);
        CREATE INDEX IF NOT EXISTS idx_agent_trust_score ON agent_trust(trust_score DESC);
    """,
    "ent_009": """
        CREATE TABLE IF NOT EXISTS namespaces (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_namespaces_user ON namespaces(user_id);

        CREATE TABLE IF NOT EXISTS namespace_permissions (
            id TEXT PRIMARY KEY,
            namespace_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            granted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT,
            FOREIGN KEY (namespace_id) REFERENCES namespaces(id),
            UNIQUE(namespace_id, user_id, agent_id, capability)
        );
        CREATE INDEX IF NOT EXISTS idx_ns_permissions_agent ON namespace_permissions(user_id, agent_id);
        CREATE INDEX IF NOT EXISTS idx_ns_permissions_namespace ON namespace_permissions(namespace_id);
    """,
    "ent_010": """
        CREATE TABLE IF NOT EXISTS agent_policies (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            allowed_confidentiality_scopes TEXT DEFAULT '[]',
            allowed_capabilities TEXT DEFAULT '[]',
            allowed_namespaces TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, agent_id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_policies_user ON agent_policies(user_id);
        CREATE INDEX IF NOT EXISTS idx_agent_policies_agent ON agent_policies(agent_id);
    """,
}


def extend_schema(db_path: str) -> None:
    """Add enterprise governance tables to an existing engram-memory SQLite DB.

    Safe to call multiple times — uses IF NOT EXISTS and a migration table.
    """
    with _lock:
        conn = sqlite3.connect(db_path)
        try:
            # Ensure migration tracking table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

            for version, ddl in ENTERPRISE_MIGRATIONS.items():
                row = conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if row is None:
                    conn.executescript(ddl)
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                        (version,),
                    )
            conn.commit()
        finally:
            conn.close()
