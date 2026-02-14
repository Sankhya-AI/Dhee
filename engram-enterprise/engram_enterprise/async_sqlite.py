"""Async SQLite manager using aiosqlite.

Provides native async database operations for the Engram memory layer.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False


class AsyncSQLiteManager:
    """Async SQLite database manager for Engram memories."""

    def __init__(self, db_path: str):
        if not HAS_AIOSQLITE:
            raise ImportError("aiosqlite is required for async support. Install with: pip install aiosqlite")

        self.db_path = db_path
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the database schema."""
        if self._initialized:
            return

        async with self._get_connection() as conn:
            await conn.executescript(
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
                    tombstone INTEGER DEFAULT 0,
                    namespace TEXT DEFAULT 'default',
                    confidentiality_scope TEXT DEFAULT 'work',
                    importance REAL DEFAULT 0.5
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
                """
            )
            await conn.commit()

        self._initialized = True

    @asynccontextmanager
    async def _get_connection(self):
        """Get a database connection with WAL mode and busy timeout."""
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            await conn.close()

    async def add_memory(
        self,
        memory_id: str,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        app_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        categories: Optional[List[str]] = None,
        immutable: bool = False,
        expiration_date: Optional[str] = None,
        layer: str = "sml",
        strength: float = 1.0,
        embedding: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """Add a memory to the database."""
        await self.initialize()

        async with self._get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                INSERT INTO memories (
                    id, memory, user_id, agent_id, run_id, app_id,
                    metadata, categories, immutable, expiration_date,
                    created_at, updated_at, layer, strength, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    content,
                    user_id,
                    agent_id,
                    run_id,
                    app_id,
                    json.dumps(metadata or {}),
                    json.dumps(categories or []),
                    1 if immutable else 0,
                    expiration_date,
                    now,
                    now,
                    layer,
                    strength,
                    json.dumps(embedding) if embedding else None,
                ),
            )
            await conn.commit()

        return {"id": memory_id, "status": "added"}

    async def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        await self.initialize()

        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM memories WHERE id = ? AND tombstone = 0",
                (memory_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                return None

            return self._row_to_dict(row)

    async def get_all_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        layer: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get all memories matching filters."""
        await self.initialize()

        conditions = ["tombstone = 0"]
        params = []

        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if layer:
            conditions.append("layer = ?")
            params.append(layer)

        query = f"""
            SELECT * FROM memories
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(limit)

        async with self._get_connection() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    async def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        strength: Optional[float] = None,
        layer: Optional[str] = None,
        access_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update a memory."""
        await self.initialize()

        updates = ["updated_at = ?"]
        params = [datetime.now(timezone.utc).isoformat()]

        if content is not None:
            updates.append("memory = ?")
            params.append(content)
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata))
        if strength is not None:
            updates.append("strength = ?")
            params.append(strength)
        if layer is not None:
            updates.append("layer = ?")
            params.append(layer)
        if access_count is not None:
            updates.append("access_count = ?")
            params.append(access_count)

        params.append(memory_id)

        async with self._get_connection() as conn:
            await conn.execute(
                f"UPDATE memories SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await conn.commit()

        return {"id": memory_id, "status": "updated"}

    async def delete_memory(self, memory_id: str, use_tombstone: bool = True) -> None:
        """Delete a memory."""
        await self.initialize()

        async with self._get_connection() as conn:
            if use_tombstone:
                await conn.execute(
                    "UPDATE memories SET tombstone = 1 WHERE id = ?",
                    (memory_id,),
                )
            else:
                await conn.execute(
                    "DELETE FROM memories WHERE id = ?",
                    (memory_id,),
                )
            await conn.commit()

    async def increment_access(self, memory_id: str) -> int:
        """Increment access count and return new value."""
        await self.initialize()

        async with self._get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                UPDATE memories
                SET access_count = access_count + 1, last_accessed = ?
                WHERE id = ?
                """,
                (now, memory_id),
            )
            await conn.commit()

            cursor = await conn.execute(
                "SELECT access_count FROM memories WHERE id = ?",
                (memory_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def add_history(
        self,
        memory_id: str,
        event: str,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        old_strength: Optional[float] = None,
        new_strength: Optional[float] = None,
        old_layer: Optional[str] = None,
        new_layer: Optional[str] = None,
    ) -> None:
        """Add a history entry."""
        await self.initialize()

        async with self._get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO memory_history (
                    memory_id, event, old_value, new_value,
                    old_strength, new_strength, old_layer, new_layer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    event,
                    old_value,
                    new_value,
                    old_strength,
                    new_strength,
                    old_layer,
                    new_layer,
                ),
            )
            await conn.commit()

    async def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        """Get history for a memory."""
        await self.initialize()

        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM memory_history
                WHERE memory_id = ?
                ORDER BY timestamp DESC
                """,
                (memory_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_stats(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get memory statistics."""
        await self.initialize()

        conditions = ["tombstone = 0"]
        params = []

        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)

        where_clause = " AND ".join(conditions)

        async with self._get_connection() as conn:
            # Total count
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM memories WHERE {where_clause}",
                params,
            )
            total = (await cursor.fetchone())[0]

            # SML count
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM memories WHERE {where_clause} AND layer = 'sml'",
                params,
            )
            sml_count = (await cursor.fetchone())[0]

            # LML count
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM memories WHERE {where_clause} AND layer = 'lml'",
                params,
            )
            lml_count = (await cursor.fetchone())[0]

            return {
                "total": total,
                "sml_count": sml_count,
                "lml_count": lml_count,
            }

    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert a database row to a dictionary."""
        result = dict(row)

        # Parse JSON fields
        if "metadata" in result and result["metadata"]:
            try:
                result["metadata"] = json.loads(result["metadata"])
            except (json.JSONDecodeError, TypeError):
                result["metadata"] = {}

        if "categories" in result and result["categories"]:
            try:
                result["categories"] = json.loads(result["categories"])
            except (json.JSONDecodeError, TypeError):
                result["categories"] = []

        if "embedding" in result and result["embedding"]:
            try:
                result["embedding"] = json.loads(result["embedding"])
            except (json.JSONDecodeError, TypeError):
                result["embedding"] = None

        return result

    async def close(self) -> None:
        """No-op for per-call connection model. Exists for interface compatibility."""
        pass
