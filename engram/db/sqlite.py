import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Phase 5: Allowed column names for dynamic UPDATE queries to prevent SQL injection.
VALID_MEMORY_COLUMNS = frozenset({
    "memory", "metadata", "categories", "embedding", "strength",
    "layer", "tombstone", "updated_at", "related_memories", "source_memories",
    "confidentiality_scope", "source_type", "source_app", "source_event_id",
    "decay_lambda", "status", "importance", "sensitivity", "namespace",
    "access_count", "last_accessed", "immutable", "expiration_date",
    "scene_id", "user_id", "agent_id", "run_id", "app_id",
    "memory_type", "s_fast", "s_mid", "s_slow",
})

VALID_SCENE_COLUMNS = frozenset({
    "title", "summary", "topic", "location", "participants", "memory_ids",
    "start_time", "end_time", "embedding", "strength", "access_count",
    "tombstone", "layer", "scene_strength", "topic_embedding_ref", "namespace",
})

VALID_PROFILE_COLUMNS = frozenset({
    "name", "profile_type", "narrative", "facts", "preferences",
    "relationships", "sentiment", "theory_of_mind", "aliases",
    "embedding", "strength", "updated_at", "role_bias", "profile_summary",
})


def _utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    """Return current UTC time as ISO string."""
    return _utcnow().isoformat()


class SQLiteManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        # Phase 1: Persistent connection with WAL mode.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_db()

    def close(self) -> None:
        """Close the persistent connection for clean shutdown."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"SQLiteManager(db_path={self.db_path!r})"

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
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
                    layer TEXT DEFAULT 'sml' CHECK (layer IN ('sml', 'lml')),
                    strength REAL DEFAULT 1.0,
                    access_count INTEGER DEFAULT 0,
                    last_accessed TEXT DEFAULT CURRENT_TIMESTAMP,
                    embedding TEXT,
                    related_memories TEXT DEFAULT '[]',
                    source_memories TEXT DEFAULT '[]',
                    tombstone INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_user_layer ON memories(user_id, layer);
                CREATE INDEX IF NOT EXISTS idx_strength ON memories(strength DESC);
                CREATE INDEX IF NOT EXISTS idx_tombstone ON memories(tombstone);

                CREATE TABLE IF NOT EXISTS memory_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    old_strength REAL,
                    new_strength REAL,
                    old_layer TEXT,
                    new_layer TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS decay_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    memories_decayed INTEGER,
                    memories_forgotten INTEGER,
                    memories_promoted INTEGER,
                    storage_before_mb REAL,
                    storage_after_mb REAL
                );

                -- CategoryMem tables
                CREATE TABLE IF NOT EXISTS categories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    category_type TEXT DEFAULT 'dynamic',
                    parent_id TEXT,
                    children_ids TEXT DEFAULT '[]',
                    memory_count INTEGER DEFAULT 0,
                    total_strength REAL DEFAULT 0.0,
                    access_count INTEGER DEFAULT 0,
                    last_accessed TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    embedding TEXT,
                    keywords TEXT DEFAULT '[]',
                    summary TEXT,
                    summary_updated_at TEXT,
                    related_ids TEXT DEFAULT '[]',
                    strength REAL DEFAULT 1.0,
                    FOREIGN KEY (parent_id) REFERENCES categories(id)
                );

                CREATE INDEX IF NOT EXISTS idx_category_type ON categories(category_type);
                CREATE INDEX IF NOT EXISTS idx_category_parent ON categories(parent_id);
                CREATE INDEX IF NOT EXISTS idx_category_strength ON categories(strength DESC);

                -- Episodic scenes
                CREATE TABLE IF NOT EXISTS scenes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    title TEXT,
                    summary TEXT,
                    topic TEXT,
                    location TEXT,
                    participants TEXT DEFAULT '[]',
                    memory_ids TEXT DEFAULT '[]',
                    start_time TEXT,
                    end_time TEXT,
                    embedding TEXT,
                    strength REAL DEFAULT 1.0,
                    access_count INTEGER DEFAULT 0,
                    tombstone INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_scene_user ON scenes(user_id);
                CREATE INDEX IF NOT EXISTS idx_scene_start ON scenes(start_time DESC);

                -- Scene-Memory junction
                CREATE TABLE IF NOT EXISTS scene_memories (
                    scene_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    position INTEGER DEFAULT 0,
                    PRIMARY KEY (scene_id, memory_id),
                    FOREIGN KEY (scene_id) REFERENCES scenes(id),
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                );

                -- Character profiles
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    name TEXT NOT NULL,
                    profile_type TEXT DEFAULT 'contact' CHECK (profile_type IN ('self', 'contact', 'entity')),
                    narrative TEXT,
                    facts TEXT DEFAULT '[]',
                    preferences TEXT DEFAULT '[]',
                    relationships TEXT DEFAULT '[]',
                    sentiment TEXT,
                    theory_of_mind TEXT DEFAULT '{}',
                    aliases TEXT DEFAULT '[]',
                    embedding TEXT,
                    strength REAL DEFAULT 1.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_profile_user ON profiles(user_id);
                CREATE INDEX IF NOT EXISTS idx_profile_name ON profiles(name);
                CREATE INDEX IF NOT EXISTS idx_profile_type ON profiles(profile_type);

                -- Profile-Memory junction
                CREATE TABLE IF NOT EXISTS profile_memories (
                    profile_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    role TEXT DEFAULT 'mentioned' CHECK (role IN ('subject', 'mentioned', 'about')),
                    PRIMARY KEY (profile_id, memory_id),
                    FOREIGN KEY (profile_id) REFERENCES profiles(id),
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                );
                """
            )
            # Legacy migration: add scene_id column to memories if missing.
            self._migrate_add_column_conn(conn, "memories", "scene_id", "TEXT")
            # v2 schema + idempotent migrations.
            self._ensure_v2_schema(conn)

    @contextmanager
    def _get_connection(self):
        """Yield the persistent connection under the thread lock."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _ensure_v2_schema(self, conn: sqlite3.Connection) -> None:
        """Create and migrate Engram v2 schema in-place (idempotent)."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        migrations: Dict[str, str] = {
            "v2_013": """
                CREATE TABLE IF NOT EXISTS distillation_provenance (
                    id TEXT PRIMARY KEY,
                    semantic_memory_id TEXT NOT NULL,
                    episodic_memory_id TEXT NOT NULL,
                    distillation_run_id TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_distill_prov_semantic ON distillation_provenance(semantic_memory_id);
                CREATE INDEX IF NOT EXISTS idx_distill_prov_episodic ON distillation_provenance(episodic_memory_id);
                CREATE INDEX IF NOT EXISTS idx_distill_prov_run ON distillation_provenance(distillation_run_id);

                CREATE TABLE IF NOT EXISTS distillation_log (
                    id TEXT PRIMARY KEY,
                    run_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    user_id TEXT,
                    episodes_sampled INTEGER DEFAULT 0,
                    semantic_created INTEGER DEFAULT 0,
                    semantic_deduplicated INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_distill_log_user ON distillation_log(user_id, run_at DESC);
            """,
        }

        for version, ddl in migrations.items():
            if not self._is_migration_applied(conn, version):
                conn.executescript(ddl)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                    (version,),
                )

        # Phase 3: Skip column migrations + backfills if already complete.
        if self._is_migration_applied(conn, "v2_columns_complete"):
            # CLS Distillation Memory columns (idempotent).
            self._ensure_cls_columns(conn)
            return

        # v2 columns on existing canonical tables.
        self._migrate_add_column_conn(conn, "memories", "confidentiality_scope", "TEXT DEFAULT 'work'")
        self._migrate_add_column_conn(conn, "memories", "source_type", "TEXT")
        self._migrate_add_column_conn(conn, "memories", "source_app", "TEXT")
        self._migrate_add_column_conn(conn, "memories", "source_event_id", "TEXT")
        self._migrate_add_column_conn(conn, "memories", "decay_lambda", "REAL DEFAULT 0.12")
        self._migrate_add_column_conn(conn, "memories", "status", "TEXT DEFAULT 'active'")
        self._migrate_add_column_conn(conn, "memories", "importance", "REAL DEFAULT 0.5")
        self._migrate_add_column_conn(conn, "memories", "sensitivity", "TEXT DEFAULT 'normal'")
        self._migrate_add_column_conn(conn, "memories", "namespace", "TEXT DEFAULT 'default'")

        self._migrate_add_column_conn(conn, "scenes", "layer", "TEXT DEFAULT 'sml'")
        self._migrate_add_column_conn(conn, "scenes", "scene_strength", "REAL DEFAULT 1.0")
        self._migrate_add_column_conn(conn, "scenes", "topic_embedding_ref", "TEXT")
        self._migrate_add_column_conn(conn, "scenes", "namespace", "TEXT DEFAULT 'default'")

        self._migrate_add_column_conn(conn, "profiles", "role_bias", "TEXT")
        self._migrate_add_column_conn(conn, "profiles", "profile_summary", "TEXT")

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_user_source_event
            ON memories(user_id, source_event_id, namespace, created_at DESC)
            """
        )

        # Backfills.
        conn.execute(
            """
            UPDATE memories
            SET confidentiality_scope = 'work'
            WHERE confidentiality_scope IS NULL OR confidentiality_scope = ''
            """
        )
        conn.execute(
            """
            UPDATE memories
            SET status = 'active'
            WHERE status IS NULL OR status = ''
            """
        )
        conn.execute(
            """
            UPDATE memories
            SET namespace = 'default'
            WHERE namespace IS NULL OR namespace = ''
            """
        )
        conn.execute(
            """
            UPDATE scenes
            SET namespace = 'default'
            WHERE namespace IS NULL OR namespace = ''
            """
        )
        conn.execute(
            """
            UPDATE memories
            SET decay_lambda = 0.12
            WHERE decay_lambda IS NULL
            """
        )
        conn.execute(
            """
            UPDATE memories
            SET importance = COALESCE(
                CASE
                    WHEN json_extract(metadata, '$.importance') IS NOT NULL
                    THEN json_extract(metadata, '$.importance')
                    ELSE importance
                END,
                0.5
            )
            """
        )
        conn.execute(
            """
            UPDATE memories
            SET sensitivity = CASE
                WHEN lower(memory) LIKE '%password%' OR lower(memory) LIKE '%api key%' OR lower(memory) LIKE '%token%'
                    THEN 'secret'
                WHEN lower(memory) LIKE '%health%' OR lower(memory) LIKE '%medical%'
                    THEN 'sensitive'
                WHEN lower(memory) LIKE '%bank%' OR lower(memory) LIKE '%salary%' OR lower(memory) LIKE '%credit card%'
                    THEN 'sensitive'
                ELSE COALESCE(NULLIF(sensitivity, ''), 'normal')
            END
            """
        )

        # Phase 3: Mark column migrations + backfills as complete.
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES ('v2_columns_complete')"
        )

        # CLS Distillation Memory columns (idempotent).
        self._ensure_cls_columns(conn)

    def _ensure_cls_columns(self, conn: sqlite3.Connection) -> None:
        """Add CLS Distillation Memory columns to memories table (idempotent)."""
        if self._is_migration_applied(conn, "v2_cls_columns_complete"):
            return

        self._migrate_add_column_conn(conn, "memories", "memory_type", "TEXT DEFAULT 'semantic'")
        self._migrate_add_column_conn(conn, "memories", "s_fast", "REAL DEFAULT NULL")
        self._migrate_add_column_conn(conn, "memories", "s_mid", "REAL DEFAULT NULL")
        self._migrate_add_column_conn(conn, "memories", "s_slow", "REAL DEFAULT NULL")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_memory_type ON memories(memory_type, user_id)"
        )

        # Backfill: set memory_type to 'semantic' for existing memories.
        conn.execute(
            "UPDATE memories SET memory_type = 'semantic' WHERE memory_type IS NULL"
        )

        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES ('v2_cls_columns_complete')"
        )

    def _is_migration_applied(self, conn: sqlite3.Connection, version: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (version,),
        ).fetchone()
        return row is not None

    # Phase 5: Allowed table names for ALTER TABLE to prevent SQL injection.
    _ALLOWED_TABLES = frozenset({
        "memories", "scenes", "profiles", "categories",
    })

    def _migrate_add_column_conn(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        col_type: str,
    ) -> None:
        """Add a column using an existing connection, if missing."""
        if table not in self._ALLOWED_TABLES:
            raise ValueError(f"Invalid table for migration: {table!r}")
        # Validate column name: must be alphanumeric/underscore only.
        if not column.replace("_", "").isalnum():
            raise ValueError(f"Invalid column name: {column!r}")
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:
            pass

    def add_memory(self, memory_data: Dict[str, Any]) -> str:
        memory_id = memory_data.get("id", str(uuid.uuid4()))
        now = _utcnow_iso()
        metadata = memory_data.get("metadata", {}) or {}
        source_app = memory_data.get("source_app") or memory_data.get("app_id") or metadata.get("source_app")

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    id, memory, user_id, agent_id, run_id, app_id,
                    metadata, categories, immutable, expiration_date,
                    created_at, updated_at, layer, strength, access_count,
                    last_accessed, embedding, related_memories, source_memories, tombstone,
                    confidentiality_scope, namespace, source_type, source_app, source_event_id, decay_lambda,
                    status, importance, sensitivity,
                    memory_type, s_fast, s_mid, s_slow
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    memory_data.get("memory", ""),
                    memory_data.get("user_id"),
                    memory_data.get("agent_id"),
                    memory_data.get("run_id"),
                    memory_data.get("app_id"),
                    json.dumps(memory_data.get("metadata", {})),
                    json.dumps(memory_data.get("categories", [])),
                    1 if memory_data.get("immutable", False) else 0,
                    memory_data.get("expiration_date"),
                    memory_data.get("created_at", now),
                    memory_data.get("updated_at", now),
                    memory_data.get("layer", "sml"),
                    memory_data.get("strength", 1.0),
                    memory_data.get("access_count", 0),
                    memory_data.get("last_accessed", now),
                    json.dumps(memory_data.get("embedding", [])),
                    json.dumps(memory_data.get("related_memories", [])),
                    json.dumps(memory_data.get("source_memories", [])),
                    1 if memory_data.get("tombstone", False) else 0,
                    memory_data.get("confidentiality_scope", "work"),
                    memory_data.get("namespace", metadata.get("namespace", "default")),
                    memory_data.get("source_type") or metadata.get("source_type") or "mcp",
                    source_app,
                    memory_data.get("source_event_id") or metadata.get("source_event_id"),
                    memory_data.get("decay_lambda", 0.12),
                    memory_data.get("status", "active"),
                    memory_data.get("importance", metadata.get("importance", 0.5)),
                    memory_data.get("sensitivity", metadata.get("sensitivity", "normal")),
                    memory_data.get("memory_type", "semantic"),
                    memory_data.get("s_fast"),
                    memory_data.get("s_mid"),
                    memory_data.get("s_slow"),
                ),
            )

            # Log within the same transaction -- atomic with the insert.
            conn.execute(
                """
                INSERT INTO memory_history (
                    memory_id, event, old_value, new_value,
                    old_strength, new_strength, old_layer, new_layer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (memory_id, "ADD", None, memory_data.get("memory"), None, None, None, None),
            )

        return memory_id

    def add_memories_batch(self, memories: List[Dict[str, Any]]) -> List[str]:
        """Insert multiple memories in a single transaction (atomic).

        Returns list of memory IDs in the same order as input.
        """
        if not memories:
            return []
        now = _utcnow_iso()
        ids: List[str] = []
        insert_rows = []
        history_rows = []

        for memory_data in memories:
            memory_id = memory_data.get("id", str(uuid.uuid4()))
            ids.append(memory_id)
            metadata = memory_data.get("metadata", {}) or {}
            source_app = memory_data.get("source_app") or memory_data.get("app_id") or metadata.get("source_app")

            insert_rows.append((
                memory_id,
                memory_data.get("memory", ""),
                memory_data.get("user_id"),
                memory_data.get("agent_id"),
                memory_data.get("run_id"),
                memory_data.get("app_id"),
                json.dumps(memory_data.get("metadata", {})),
                json.dumps(memory_data.get("categories", [])),
                1 if memory_data.get("immutable", False) else 0,
                memory_data.get("expiration_date"),
                memory_data.get("created_at", now),
                memory_data.get("updated_at", now),
                memory_data.get("layer", "sml"),
                memory_data.get("strength", 1.0),
                memory_data.get("access_count", 0),
                memory_data.get("last_accessed", now),
                json.dumps(memory_data.get("embedding", [])),
                json.dumps(memory_data.get("related_memories", [])),
                json.dumps(memory_data.get("source_memories", [])),
                1 if memory_data.get("tombstone", False) else 0,
                memory_data.get("confidentiality_scope", "work"),
                memory_data.get("namespace", metadata.get("namespace", "default")),
                memory_data.get("source_type") or metadata.get("source_type") or "mcp",
                source_app,
                memory_data.get("source_event_id") or metadata.get("source_event_id"),
                memory_data.get("decay_lambda", 0.12),
                memory_data.get("status", "active"),
                memory_data.get("importance", metadata.get("importance", 0.5)),
                memory_data.get("sensitivity", metadata.get("sensitivity", "normal")),
                memory_data.get("memory_type", "semantic"),
                memory_data.get("s_fast"),
                memory_data.get("s_mid"),
                memory_data.get("s_slow"),
            ))
            history_rows.append((
                memory_id, "ADD", None, memory_data.get("memory"), None, None, None, None,
            ))

        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO memories (
                    id, memory, user_id, agent_id, run_id, app_id,
                    metadata, categories, immutable, expiration_date,
                    created_at, updated_at, layer, strength, access_count,
                    last_accessed, embedding, related_memories, source_memories, tombstone,
                    confidentiality_scope, namespace, source_type, source_app, source_event_id, decay_lambda,
                    status, importance, sensitivity,
                    memory_type, s_fast, s_mid, s_slow
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                insert_rows,
            )
            conn.executemany(
                """
                INSERT INTO memory_history (
                    memory_id, event, old_value, new_value,
                    old_strength, new_strength, old_layer, new_layer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                history_rows,
            )

        return ids

    def get_memory(self, memory_id: str, include_tombstoned: bool = False) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM memories WHERE id = ?"
        params = [memory_id]
        if not include_tombstoned:
            query += " AND tombstone = 0"

        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            if row:
                return self._row_to_dict(row)
        return None

    def get_memory_by_source_event(
        self,
        *,
        user_id: str,
        source_event_id: str,
        namespace: Optional[str] = None,
        source_app: Optional[str] = None,
        include_tombstoned: bool = False,
    ) -> Optional[Dict[str, Any]]:
        normalized_event = str(source_event_id or "").strip()
        if not normalized_event:
            return None
        query = """
            SELECT *
            FROM memories
            WHERE user_id = ?
              AND source_event_id = ?
        """
        params: List[Any] = [user_id, normalized_event]
        if namespace:
            query += " AND namespace = ?"
            params.append(namespace)
        if source_app:
            query += " AND source_app = ?"
            params.append(source_app)
        if not include_tombstoned:
            query += " AND tombstone = 0"
        query += " ORDER BY created_at DESC LIMIT 1"

        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            if row:
                return self._row_to_dict(row)
        return None

    def get_all_memories(
        self,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        app_id: Optional[str] = None,
        layer: Optional[str] = None,
        namespace: Optional[str] = None,
        memory_type: Optional[str] = None,
        min_strength: float = 0.0,
        include_tombstoned: bool = False,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM memories WHERE strength >= ?"
        params: List[Any] = [min_strength]

        if not include_tombstoned:
            query += " AND tombstone = 0"
        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if app_id:
            query += " AND app_id = ?"
            params.append(app_id)
        if layer:
            query += " AND layer = ?"
            params.append(layer)
        if namespace:
            query += " AND namespace = ?"
            params.append(namespace)
        if created_after:
            query += " AND created_at >= ?"
            params.append(created_after)
        if created_before:
            query += " AND created_at <= ?"
            params.append(created_before)

        query += " ORDER BY strength DESC"

        # Apply SQL-level LIMIT to avoid fetching unbounded rows into memory.
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def update_memory(self, memory_id: str, updates: Dict[str, Any]) -> bool:
        set_clauses = []
        params: List[Any] = []
        for key, value in updates.items():
            if key not in VALID_MEMORY_COLUMNS:
                raise ValueError(f"Invalid memory column: {key!r}")
            if key in {"metadata", "categories", "embedding", "related_memories", "source_memories"}:
                value = json.dumps(value)
            set_clauses.append(f"{key} = ?")
            params.append(value)

        set_clauses.append("updated_at = ?")
        params.append(_utcnow_iso())
        params.append(memory_id)

        with self._get_connection() as conn:
            # Read old values and update in a single transaction.
            old_row = conn.execute(
                "SELECT memory, strength, layer FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if not old_row:
                return False

            conn.execute(
                f"UPDATE memories SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )

            # Log within the same transaction.
            conn.execute(
                """
                INSERT INTO memory_history (
                    memory_id, event, old_value, new_value,
                    old_strength, new_strength, old_layer, new_layer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    "UPDATE",
                    old_row["memory"],
                    updates.get("memory"),
                    old_row["strength"],
                    updates.get("strength"),
                    old_row["layer"],
                    updates.get("layer"),
                ),
            )
        return True

    def delete_memory(self, memory_id: str, use_tombstone: bool = True) -> bool:
        if use_tombstone:
            return self.update_memory(memory_id, {"tombstone": 1})
        with self._get_connection() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._log_event(memory_id, "DELETE")
        return True

    def increment_access(self, memory_id: str) -> None:
        now = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE memories
                SET access_count = access_count + 1, last_accessed = ?
                WHERE id = ?
                """,
                (now, memory_id),
            )

    # Phase 2: Batch operations to eliminate N+1 queries in search.

    def get_memories_bulk(self, memory_ids: List[str], include_tombstoned: bool = False) -> Dict[str, Dict[str, Any]]:
        """Fetch multiple memories by ID in a single query. Returns {id: memory_dict}."""
        if not memory_ids:
            return {}
        with self._get_connection() as conn:
            placeholders = ",".join("?" for _ in memory_ids)
            query = f"SELECT * FROM memories WHERE id IN ({placeholders})"
            if not include_tombstoned:
                query += " AND tombstone = 0"
            rows = conn.execute(query, memory_ids).fetchall()
            return {row["id"]: self._row_to_dict(row) for row in rows}

    def increment_access_bulk(self, memory_ids: List[str]) -> None:
        """Increment access count for multiple memories in a single transaction."""
        if not memory_ids:
            return
        now = _utcnow_iso()
        with self._get_connection() as conn:
            placeholders = ",".join("?" for _ in memory_ids)
            conn.execute(
                f"""
                UPDATE memories
                SET access_count = access_count + 1, last_accessed = ?
                WHERE id IN ({placeholders})
                """,
                [now] + list(memory_ids),
            )

    def update_strength_bulk(self, updates: Dict[str, float]) -> None:
        """Batch-update strength for multiple memories. updates = {memory_id: new_strength}."""
        if not updates:
            return
        now = _utcnow_iso()
        with self._get_connection() as conn:
            conn.executemany(
                "UPDATE memories SET strength = ?, updated_at = ? WHERE id = ?",
                [(strength, now, memory_id) for memory_id, strength in updates.items()],
            )

    _MEMORY_JSON_FIELDS = ("metadata", "categories", "related_memories", "source_memories")

    def _row_to_dict(self, row: sqlite3.Row, *, skip_embedding: bool = False) -> Dict[str, Any]:
        data = dict(row)
        for key in self._MEMORY_JSON_FIELDS:
            if key in data and data[key]:
                data[key] = json.loads(data[key])
        # Embedding is the largest JSON field (~30-50KB for 3072-dim vectors).
        # Skip deserialization when the caller doesn't need it.
        if skip_embedding:
            data.pop("embedding", None)
        elif "embedding" in data and data["embedding"]:
            data["embedding"] = json.loads(data["embedding"])
        data["immutable"] = bool(data.get("immutable", 0))
        data["tombstone"] = bool(data.get("tombstone", 0))
        return data

    def _log_event(self, memory_id: str, event: str, **kwargs: Any) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO memory_history (
                    memory_id, event, old_value, new_value,
                    old_strength, new_strength, old_layer, new_layer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    event,
                    kwargs.get("old_value"),
                    kwargs.get("new_value"),
                    kwargs.get("old_strength"),
                    kwargs.get("new_strength"),
                    kwargs.get("old_layer"),
                    kwargs.get("new_layer"),
                ),
            )

    def log_event(self, memory_id: str, event: str, **kwargs: Any) -> None:
        """Public wrapper for logging custom events like DECAY or FUSE."""
        self._log_event(memory_id, event, **kwargs)

    def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_history WHERE memory_id = ? ORDER BY timestamp DESC",
                (memory_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def log_decay(self, decayed: int, forgotten: int, promoted: int, storage_before_mb: Optional[float] = None, storage_after_mb: Optional[float] = None) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO decay_log (memories_decayed, memories_forgotten, memories_promoted, storage_before_mb, storage_after_mb)
                VALUES (?, ?, ?, ?, ?)
                """,
                (decayed, forgotten, promoted, storage_before_mb, storage_after_mb),
            )

    def purge_tombstoned(self) -> int:
        """Permanently delete all tombstoned memories. This is IRREVERSIBLE."""
        with self._get_connection() as conn:
            # Log what will be purged before deletion for audit trail.
            rows = conn.execute(
                "SELECT id, user_id, memory FROM memories WHERE tombstone = 1"
            ).fetchall()
            count = len(rows)
            if count > 0:
                ids = [row["id"] for row in rows]
                logger.warning(
                    "purge_tombstoned: permanently deleting %d memories: %s",
                    count,
                    ids,
                )
                for row in rows:
                    conn.execute(
                        """INSERT INTO memory_history (memory_id, event, old_value, new_value,
                           old_strength, new_strength, old_layer, new_layer)
                           VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL)""",
                        (row["id"], "PURGE", row["memory"]),
                    )
                conn.execute("DELETE FROM memories WHERE tombstone = 1")
            return count

    # CLS Distillation Memory helpers

    def get_episodic_memories(
        self,
        user_id: str,
        *,
        scene_id: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        limit: int = 100,
        namespace: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch episodic-type memories for a user, optionally filtered by scene/time."""
        query = "SELECT * FROM memories WHERE user_id = ? AND memory_type = 'episodic' AND tombstone = 0"
        params: List[Any] = [user_id]
        if scene_id:
            query += " AND scene_id = ?"
            params.append(scene_id)
        if created_after:
            query += " AND created_at >= ?"
            params.append(created_after)
        if created_before:
            query += " AND created_at <= ?"
            params.append(created_before)
        if namespace:
            query += " AND namespace = ?"
            params.append(namespace)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def add_distillation_provenance(
        self,
        semantic_memory_id: str,
        episodic_memory_ids: List[str],
        run_id: str,
    ) -> None:
        """Record which episodic memories contributed to a distilled semantic memory."""
        with self._get_connection() as conn:
            for ep_id in episodic_memory_ids:
                conn.execute(
                    """
                    INSERT INTO distillation_provenance (id, semantic_memory_id, episodic_memory_id, distillation_run_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), semantic_memory_id, ep_id, run_id),
                )

    def log_distillation_run(
        self,
        user_id: str,
        episodes_sampled: int,
        semantic_created: int,
        semantic_deduplicated: int = 0,
        errors: int = 0,
    ) -> str:
        """Log a distillation run and return the run ID."""
        run_id = str(uuid.uuid4())
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO distillation_log (id, user_id, episodes_sampled, semantic_created, semantic_deduplicated, errors)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, user_id, episodes_sampled, semantic_created, semantic_deduplicated, errors),
            )
        return run_id

    def get_memory_count_by_namespace(self, user_id: str) -> Dict[str, int]:
        """Return {namespace: count} for active memories of a user."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(namespace, 'default') AS ns, COUNT(*) AS cnt
                FROM memories
                WHERE user_id = ? AND tombstone = 0
                GROUP BY ns
                """,
                (user_id,),
            ).fetchall()
            return {row["ns"]: row["cnt"] for row in rows}

    def update_multi_trace(
        self,
        memory_id: str,
        s_fast: float,
        s_mid: float,
        s_slow: float,
        effective_strength: float,
    ) -> bool:
        """Update multi-trace columns and effective strength for a memory."""
        return self.update_memory(memory_id, {
            "s_fast": s_fast,
            "s_mid": s_mid,
            "s_slow": s_slow,
            "strength": effective_strength,
        })

    # CategoryMem methods
    def save_category(self, category_data: Dict[str, Any]) -> str:
        """Save or update a category."""
        category_id = category_data.get("id")
        if not category_id:
            return ""

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO categories (
                    id, name, description, category_type, parent_id,
                    children_ids, memory_count, total_strength, access_count,
                    last_accessed, created_at, embedding, keywords,
                    summary, summary_updated_at, related_ids, strength
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    category_id,
                    category_data.get("name", ""),
                    category_data.get("description", ""),
                    category_data.get("category_type", "dynamic"),
                    category_data.get("parent_id"),
                    json.dumps(category_data.get("children_ids", [])),
                    category_data.get("memory_count", 0),
                    category_data.get("total_strength", 0.0),
                    category_data.get("access_count", 0),
                    category_data.get("last_accessed"),
                    category_data.get("created_at"),
                    json.dumps(category_data.get("embedding")) if category_data.get("embedding") else None,
                    json.dumps(category_data.get("keywords", [])),
                    category_data.get("summary"),
                    category_data.get("summary_updated_at"),
                    json.dumps(category_data.get("related_ids", [])),
                    category_data.get("strength", 1.0),
                ),
            )
        return category_id

    def get_category(self, category_id: str) -> Optional[Dict[str, Any]]:
        """Get a category by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM categories WHERE id = ?",
                (category_id,)
            ).fetchone()
            if row:
                return self._category_row_to_dict(row)
        return None

    def get_all_categories(self) -> List[Dict[str, Any]]:
        """Get all categories."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM categories ORDER BY strength DESC"
            ).fetchall()
            return [self._category_row_to_dict(row) for row in rows]

    def delete_category(self, category_id: str) -> bool:
        """Delete a category."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        return True

    def save_all_categories(self, categories: List[Dict[str, Any]]) -> int:
        """Save multiple categories in a single transaction for performance."""
        if not categories:
            return 0
        rows = []
        for cat in categories:
            cat_id = cat.get("id")
            if not cat_id:
                continue
            rows.append((
                cat_id,
                cat.get("name", ""),
                cat.get("description", ""),
                cat.get("category_type", "dynamic"),
                cat.get("parent_id"),
                json.dumps(cat.get("children_ids", [])),
                cat.get("memory_count", 0),
                cat.get("total_strength", 0.0),
                cat.get("access_count", 0),
                cat.get("last_accessed"),
                cat.get("created_at"),
                json.dumps(cat.get("embedding")) if cat.get("embedding") else None,
                json.dumps(cat.get("keywords", [])),
                cat.get("summary"),
                cat.get("summary_updated_at"),
                json.dumps(cat.get("related_ids", [])),
                cat.get("strength", 1.0),
            ))
        if not rows:
            return 0
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO categories (
                    id, name, description, category_type, parent_id,
                    children_ids, memory_count, total_strength, access_count,
                    last_accessed, created_at, embedding, keywords,
                    summary, summary_updated_at, related_ids, strength
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def _category_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a category row to dict."""
        data = dict(row)
        for key in ["children_ids", "keywords", "related_ids"]:
            if key in data and data[key]:
                data[key] = json.loads(data[key])
            else:
                data[key] = []
        if data.get("embedding"):
            data["embedding"] = json.loads(data["embedding"])
        return data

    def _migrate_add_column(self, table: str, column: str, col_type: str) -> None:
        """Add a column to an existing table if it doesn't already exist."""
        with self._get_connection() as conn:
            self._migrate_add_column_conn(conn, table, column, col_type)

    # =========================================================================
    # Scene methods
    # =========================================================================

    def add_scene(self, scene_data: Dict[str, Any]) -> str:
        scene_id = scene_data.get("id", str(uuid.uuid4()))
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO scenes (
                    id, user_id, title, summary, topic, location,
                    participants, memory_ids, start_time, end_time,
                    embedding, strength, access_count, tombstone,
                    layer, scene_strength, topic_embedding_ref, namespace
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scene_id,
                    scene_data.get("user_id"),
                    scene_data.get("title"),
                    scene_data.get("summary"),
                    scene_data.get("topic"),
                    scene_data.get("location"),
                    json.dumps(scene_data.get("participants", [])),
                    json.dumps(scene_data.get("memory_ids", [])),
                    scene_data.get("start_time"),
                    scene_data.get("end_time"),
                    json.dumps(scene_data.get("embedding")) if scene_data.get("embedding") else None,
                    scene_data.get("strength", 1.0),
                    scene_data.get("access_count", 0),
                    1 if scene_data.get("tombstone", False) else 0,
                    scene_data.get("layer", "sml"),
                    scene_data.get("scene_strength", scene_data.get("strength", 1.0)),
                    scene_data.get("topic_embedding_ref"),
                    scene_data.get("namespace", "default"),
                ),
            )
        return scene_id

    def get_scene(self, scene_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM scenes WHERE id = ? AND tombstone = 0", (scene_id,)
            ).fetchone()
            if row:
                return self._scene_row_to_dict(row)
        return None

    def update_scene(self, scene_id: str, updates: Dict[str, Any]) -> bool:
        set_clauses = []
        params: List[Any] = []
        for key, value in updates.items():
            if key not in VALID_SCENE_COLUMNS:
                raise ValueError(f"Invalid scene column: {key!r}")
            if key in {"participants", "memory_ids", "embedding"}:
                value = json.dumps(value)
            set_clauses.append(f"{key} = ?")
            params.append(value)
        if not set_clauses:
            return False
        params.append(scene_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE scenes SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
        return True

    def get_open_scene(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get the most recent scene without an end_time for a user."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM scenes
                WHERE user_id = ? AND end_time IS NULL AND tombstone = 0
                ORDER BY start_time DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if row:
                return self._scene_row_to_dict(row)
        return None

    def get_scenes(
        self,
        user_id: Optional[str] = None,
        topic: Optional[str] = None,
        start_after: Optional[str] = None,
        start_before: Optional[str] = None,
        namespace: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM scenes WHERE tombstone = 0"
        params: List[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if topic:
            query += " AND topic LIKE ?"
            params.append(f"%{topic}%")
        if start_after:
            query += " AND start_time >= ?"
            params.append(start_after)
        if start_before:
            query += " AND start_time <= ?"
            params.append(start_before)
        if namespace:
            query += " AND namespace = ?"
            params.append(namespace)
        query += " ORDER BY start_time DESC LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._scene_row_to_dict(row) for row in rows]

    def add_scene_memory(self, scene_id: str, memory_id: str, position: int = 0) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO scene_memories (scene_id, memory_id, position) VALUES (?, ?, ?)",
                (scene_id, memory_id, position),
            )

    def get_scene_memories(self, scene_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT m.* FROM memories m
                JOIN scene_memories sm ON m.id = sm.memory_id
                WHERE sm.scene_id = ? AND m.tombstone = 0
                ORDER BY sm.position
                """,
                (scene_id,),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def _scene_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        for key in ["participants", "memory_ids"]:
            if key in data and data[key]:
                data[key] = json.loads(data[key])
            else:
                data[key] = []
        if data.get("embedding"):
            data["embedding"] = json.loads(data["embedding"])
        data["tombstone"] = bool(data.get("tombstone", 0))
        return data

    # =========================================================================
    # Profile methods
    # =========================================================================

    def add_profile(self, profile_data: Dict[str, Any]) -> str:
        profile_id = profile_data.get("id", str(uuid.uuid4()))
        now = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO profiles (
                    id, user_id, name, profile_type, narrative,
                    facts, preferences, relationships, sentiment,
                    theory_of_mind, aliases, embedding, strength,
                    created_at, updated_at, role_bias, profile_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    profile_data.get("user_id"),
                    profile_data.get("name", ""),
                    profile_data.get("profile_type", "contact"),
                    profile_data.get("narrative"),
                    json.dumps(profile_data.get("facts", [])),
                    json.dumps(profile_data.get("preferences", [])),
                    json.dumps(profile_data.get("relationships", [])),
                    profile_data.get("sentiment"),
                    json.dumps(profile_data.get("theory_of_mind", {})),
                    json.dumps(profile_data.get("aliases", [])),
                    json.dumps(profile_data.get("embedding")) if profile_data.get("embedding") else None,
                    profile_data.get("strength", 1.0),
                    profile_data.get("created_at", now),
                    profile_data.get("updated_at", now),
                    profile_data.get("role_bias"),
                    profile_data.get("profile_summary"),
                ),
            )
        return profile_id

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if row:
                return self._profile_row_to_dict(row)
        return None

    def update_profile(self, profile_id: str, updates: Dict[str, Any]) -> bool:
        set_clauses = []
        params: List[Any] = []
        for key, value in updates.items():
            if key not in VALID_PROFILE_COLUMNS:
                raise ValueError(f"Invalid profile column: {key!r}")
            if key in {"facts", "preferences", "relationships", "aliases", "theory_of_mind", "embedding"}:
                value = json.dumps(value)
            set_clauses.append(f"{key} = ?")
            params.append(value)
        set_clauses.append("updated_at = ?")
        params.append(_utcnow_iso())
        params.append(profile_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE profiles SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
        return True

    def get_all_profiles(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM profiles"
        params: List[Any] = []
        if user_id:
            query += " WHERE user_id = ?"
            params.append(user_id)
        query += " ORDER BY strength DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._profile_row_to_dict(row) for row in rows]

    def get_profile_by_name(self, name: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Find a profile by exact name match, then fall back to alias scan."""
        # Fast path: exact name match via indexed column.
        query = "SELECT * FROM profiles WHERE lower(name) = ?"
        params: List[Any] = [name.lower()]
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " LIMIT 1"
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            if row:
                return self._profile_row_to_dict(row)
            # Slow path: alias scan (aliases stored as JSON, can't index).
            alias_query = "SELECT * FROM profiles WHERE aliases LIKE ?"
            alias_params: List[Any] = [f'%"{name}"%']
            if user_id:
                alias_query += " AND user_id = ?"
                alias_params.append(user_id)
            alias_query += " LIMIT 1"
            row = conn.execute(alias_query, alias_params).fetchone()
            if row:
                result = self._profile_row_to_dict(row)
                # Verify case-insensitive alias match.
                if name.lower() in [a.lower() for a in result.get("aliases", [])]:
                    return result
        return None

    def find_profile_by_substring(self, name: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Find a profile where the name contains the query as a substring (case-insensitive)."""
        query = "SELECT * FROM profiles WHERE lower(name) LIKE ?"
        params: List[Any] = [f"%{name.lower()}%"]
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " ORDER BY strength DESC LIMIT 1"
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            if row:
                return self._profile_row_to_dict(row)
        return None

    def add_profile_memory(self, profile_id: str, memory_id: str, role: str = "mentioned") -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO profile_memories (profile_id, memory_id, role) VALUES (?, ?, ?)",
                (profile_id, memory_id, role),
            )

    def get_profile_memories(self, profile_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT m.*, pm.role AS profile_role FROM memories m
                JOIN profile_memories pm ON m.id = pm.memory_id
                WHERE pm.profile_id = ? AND m.tombstone = 0
                ORDER BY m.created_at DESC
                """,
                (profile_id,),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def _profile_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        for key in ["facts", "preferences", "relationships", "aliases"]:
            if key in data and data[key]:
                data[key] = json.loads(data[key])
            else:
                data[key] = []
        if data.get("theory_of_mind"):
            data["theory_of_mind"] = json.loads(data["theory_of_mind"])
        else:
            data["theory_of_mind"] = {}
        if data.get("embedding"):
            data["embedding"] = json.loads(data["embedding"])
        return data

    def get_memories_by_category(
        self,
        category_id: str,
        limit: int = 100,
        min_strength: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Get memories belonging to a specific category."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE categories LIKE ? AND strength >= ? AND tombstone = 0
                ORDER BY strength DESC
                LIMIT ?
                """,
                (f'%"{category_id}"%', min_strength, limit),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    # =========================================================================
    # User ID listing
    # =========================================================================

    def list_user_ids(self) -> List[str]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT user_id FROM memories
                WHERE user_id IS NOT NULL AND user_id != ''
                ORDER BY user_id
                """
            ).fetchall()
        return [str(row["user_id"]) for row in rows if row["user_id"]]

    # =========================================================================
    # Dashboard / Visualization methods
    # =========================================================================

    def get_constellation_data(self, user_id: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        """Build graph data for the constellation visualizer."""
        with self._get_connection() as conn:
            # Nodes: memories
            mem_query = "SELECT id, memory, strength, layer, categories, created_at FROM memories WHERE tombstone = 0"
            params: List[Any] = []
            if user_id:
                mem_query += " AND user_id = ?"
                params.append(user_id)
            mem_query += " ORDER BY strength DESC LIMIT ?"
            params.append(limit)
            mem_rows = conn.execute(mem_query, params).fetchall()

            nodes = []
            node_ids = set()
            for row in mem_rows:
                cats = row["categories"]
                if cats:
                    try:
                        cats = json.loads(cats)
                    except Exception:
                        cats = []
                else:
                    cats = []
                nodes.append({
                    "id": row["id"],
                    "memory": (row["memory"] or "")[:120],
                    "strength": row["strength"],
                    "layer": row["layer"],
                    "categories": cats,
                    "created_at": row["created_at"],
                })
                node_ids.add(row["id"])

            # Edges from scene_memories (memories sharing a scene)
            edges: List[Dict[str, Any]] = []
            if node_ids:
                placeholders = ",".join("?" for _ in node_ids)
                scene_rows = conn.execute(
                    f"""
                    SELECT a.memory_id AS source, b.memory_id AS target, a.scene_id
                    FROM scene_memories a
                    JOIN scene_memories b ON a.scene_id = b.scene_id AND a.memory_id < b.memory_id
                    WHERE a.memory_id IN ({placeholders}) AND b.memory_id IN ({placeholders})
                    """,
                    list(node_ids) + list(node_ids),
                ).fetchall()
                for row in scene_rows:
                    edges.append({"source": row["source"], "target": row["target"], "type": "scene"})

                # Edges from profile_memories (memories sharing a profile)
                profile_rows = conn.execute(
                    f"""
                    SELECT a.memory_id AS source, b.memory_id AS target, a.profile_id
                    FROM profile_memories a
                    JOIN profile_memories b ON a.profile_id = b.profile_id AND a.memory_id < b.memory_id
                    WHERE a.memory_id IN ({placeholders}) AND b.memory_id IN ({placeholders})
                    """,
                    list(node_ids) + list(node_ids),
                ).fetchall()
                for row in profile_rows:
                    edges.append({"source": row["source"], "target": row["target"], "type": "profile"})

        return {"nodes": nodes, "edges": edges}

    def get_decay_log_entries(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent decay log entries for the dashboard sparkline."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM decay_log ORDER BY run_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # =========================================================================
    # Utilities
    # =========================================================================

    @staticmethod
    def _parse_json_value(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return default
