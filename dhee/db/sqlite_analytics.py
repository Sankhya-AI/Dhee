import hashlib
import json
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from .sqlite_common import _utcnow_iso


class SQLiteAnalyticsMixin:
    """Distillation, episodic indexing, counters, and aggregate APIs."""

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
        """Fetch episodic-type memories for a user, optionally filtered."""
        query = (
            "SELECT * FROM memories WHERE user_id = ? "
            "AND memory_type = 'episodic' AND tombstone = 0"
        )
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
        """Record which episodic memories contributed to a semantic memory."""
        with self._get_connection() as conn:
            for ep_id in episodic_memory_ids:
                conn.execute(
                    """
                    INSERT INTO distillation_provenance (
                        id, semantic_memory_id, episodic_memory_id,
                        distillation_run_id
                    ) VALUES (?, ?, ?, ?)
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
                INSERT INTO distillation_log (
                    id, user_id, episodes_sampled, semantic_created,
                    semantic_deduplicated, errors
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    user_id,
                    episodes_sampled,
                    semantic_created,
                    semantic_deduplicated,
                    errors,
                ),
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
        return self.update_memory(
            memory_id,
            {
                "s_fast": s_fast,
                "s_mid": s_mid,
                "s_slow": s_slow,
                "strength": effective_strength,
            },
        )

    def delete_episodic_events_for_memory(self, memory_id: str) -> int:
        with self._get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM episodic_events WHERE memory_id = ?",
                (memory_id,),
            )
            return int(cur.rowcount or 0)

    def add_episodic_events(self, events: List[Dict[str, Any]]) -> int:
        if not events:
            return 0
        rows = []
        for event in events:
            rows.append(
                (
                    event.get("id"),
                    event.get("memory_id"),
                    event.get("user_id"),
                    event.get("conversation_id"),
                    event.get("session_id"),
                    int(event.get("turn_id", 0) or 0),
                    event.get("actor_id"),
                    event.get("actor_role"),
                    event.get("event_time"),
                    event.get("event_type"),
                    event.get("canonical_key"),
                    event.get("value_text"),
                    event.get("value_num"),
                    event.get("value_unit"),
                    event.get("currency"),
                    event.get("normalized_time_start"),
                    event.get("normalized_time_end"),
                    event.get("time_granularity"),
                    event.get("entity_key"),
                    event.get("value_norm"),
                    event.get("confidence", 0.0),
                    event.get("superseded_by"),
                )
            )
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO episodic_events (
                    id, memory_id, user_id, conversation_id, session_id, turn_id,
                    actor_id, actor_role, event_time, event_type, canonical_key,
                    value_text, value_num, value_unit, currency,
                    normalized_time_start, normalized_time_end, time_granularity,
                    entity_key, value_norm, confidence, superseded_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_episodic_events(
        self,
        *,
        user_id: str,
        actor_id: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        time_anchor: Optional[str] = None,
        entity_hints: Optional[List[str]] = None,
        include_superseded: bool = False,
        limit: int = 300,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM episodic_events WHERE user_id = ?"
        params: List[Any] = [user_id]
        if actor_id:
            query += " AND actor_id = ?"
            params.append(actor_id)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            query += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        if time_anchor:
            query += " AND COALESCE(normalized_time_start, event_time) <= ?"
            params.append(str(time_anchor))
        normalized_hints = [
            str(h).strip().lower()
            for h in (entity_hints or [])
            if str(h).strip()
        ]
        if normalized_hints:
            clauses = []
            for hint in normalized_hints:
                wildcard = f"%{hint}%"
                clauses.append(
                    "("
                    "LOWER(COALESCE(entity_key, '')) LIKE ? "
                    "OR LOWER(COALESCE(actor_id, '')) LIKE ? "
                    "OR LOWER(COALESCE(actor_role, '')) LIKE ?"
                    ")"
                )
                params.extend([wildcard, wildcard, wildcard])
            query += " AND (" + " OR ".join(clauses) + ")"
        if not include_superseded:
            query += " AND (superseded_by IS NULL OR superseded_by = '')"
        query += (
            " ORDER BY COALESCE(normalized_time_start, event_time) DESC, "
            "turn_id DESC LIMIT ?"
        )
        params.append(max(1, int(limit)))
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def record_cost_counter(
        self,
        *,
        phase: str,
        user_id: Optional[str] = None,
        llm_calls: float = 0.0,
        input_tokens: float = 0.0,
        output_tokens: float = 0.0,
        embed_calls: float = 0.0,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO cost_counters (
                    user_id, phase, llm_calls, input_tokens, output_tokens,
                    embed_calls
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    str(phase),
                    float(llm_calls or 0.0),
                    float(input_tokens or 0.0),
                    float(output_tokens or 0.0),
                    float(embed_calls or 0.0),
                ),
            )

    def aggregate_cost_counters(
        self,
        *,
        phase: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = """
            SELECT
                COUNT(*) AS samples,
                COALESCE(SUM(llm_calls), 0) AS llm_calls,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(embed_calls), 0) AS embed_calls
            FROM cost_counters
            WHERE phase = ?
        """
        params: List[Any] = [str(phase)]
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
        if not row:
            return {
                "phase": phase,
                "samples": 0,
                "llm_calls": 0.0,
                "input_tokens": 0.0,
                "output_tokens": 0.0,
                "embed_calls": 0.0,
            }
        return {
            "phase": phase,
            "samples": int(row["samples"] or 0),
            "llm_calls": float(row["llm_calls"] or 0.0),
            "input_tokens": float(row["input_tokens"] or 0.0),
            "output_tokens": float(row["output_tokens"] or 0.0),
            "embed_calls": float(row["embed_calls"] or 0.0),
        }

    def get_constellation_data(
        self,
        user_id: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Build graph data for the constellation visualizer."""
        with self._get_connection() as conn:
            mem_query = (
                "SELECT id, memory, strength, layer, categories, created_at "
                "FROM memories WHERE tombstone = 0"
            )
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
                nodes.append(
                    {
                        "id": row["id"],
                        "memory": (row["memory"] or "")[:120],
                        "strength": row["strength"],
                        "layer": row["layer"],
                        "categories": self._parse_json_value(
                            row["categories"], []
                        ),
                        "created_at": row["created_at"],
                    }
                )
                node_ids.add(row["id"])

            edges: List[Dict[str, Any]] = []
            if node_ids:
                placeholders = ",".join("?" for _ in node_ids)
                scene_rows = conn.execute(
                    f"""
                    SELECT a.memory_id AS source, b.memory_id AS target, a.scene_id
                    FROM scene_memories a
                    JOIN scene_memories b
                        ON a.scene_id = b.scene_id
                        AND a.memory_id < b.memory_id
                    WHERE a.memory_id IN ({placeholders})
                        AND b.memory_id IN ({placeholders})
                    """,
                    list(node_ids) + list(node_ids),
                ).fetchall()
                for row in scene_rows:
                    edges.append(
                        {
                            "source": row["source"],
                            "target": row["target"],
                            "type": "scene",
                        }
                    )

                profile_rows = conn.execute(
                    f"""
                    SELECT a.memory_id AS source, b.memory_id AS target, a.profile_id
                    FROM profile_memories a
                    JOIN profile_memories b
                        ON a.profile_id = b.profile_id
                        AND a.memory_id < b.memory_id
                    WHERE a.memory_id IN ({placeholders})
                        AND b.memory_id IN ({placeholders})
                    """,
                    list(node_ids) + list(node_ids),
                ).fetchall()
                for row in profile_rows:
                    edges.append(
                        {
                            "source": row["source"],
                            "target": row["target"],
                            "type": "profile",
                        }
                    )

        return {"nodes": nodes, "edges": edges}

    def get_decay_log_entries(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent decay log entries for the dashboard sparkline."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM decay_log ORDER BY run_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _ensure_entity_table(self, conn: sqlite3.Connection) -> None:
        """Lazily ensure entity_aggregates table exists."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_aggregates (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                agg_type TEXT NOT NULL,
                value_num REAL DEFAULT 0.0,
                value_unit TEXT,
                item_set TEXT,
                contributing_sessions TEXT,
                contributing_memory_ids TEXT,
                last_updated TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entity_agg_lookup
                ON entity_aggregates(user_id, agg_type, entity_key)
            """
        )

    def upsert_entity_aggregate(
        self,
        user_id: str,
        entity_key: str,
        agg_type: str,
        value_delta: float,
        value_unit: Optional[str] = None,
        session_id: Optional[str] = None,
        memory_id: Optional[str] = None,
    ) -> None:
        """Increment an entity aggregate and append provenance metadata."""
        agg_id = hashlib.sha256(
            f"{user_id}|{agg_type}|{entity_key}".encode()
        ).hexdigest()
        now = _utcnow_iso()
        with self._get_connection() as conn:
            self._ensure_entity_table(conn)
            existing = conn.execute(
                """
                SELECT value_num, contributing_sessions, contributing_memory_ids
                FROM entity_aggregates WHERE id = ?
                """,
                (agg_id,),
            ).fetchone()
            if existing:
                cur_val = float(existing["value_num"] or 0)
                sessions = self._parse_json_value(
                    existing["contributing_sessions"], []
                )
                memories = self._parse_json_value(
                    existing["contributing_memory_ids"], []
                )
                if session_id and session_id not in sessions:
                    sessions.append(session_id)
                if memory_id and memory_id not in memories:
                    memories.append(memory_id)
                conn.execute(
                    """
                    UPDATE entity_aggregates
                    SET value_num = ?, value_unit = COALESCE(?, value_unit),
                        contributing_sessions = ?, contributing_memory_ids = ?,
                        last_updated = ?
                    WHERE id = ?
                    """,
                    (
                        cur_val + value_delta,
                        value_unit,
                        json.dumps(sessions),
                        json.dumps(memories),
                        now,
                        agg_id,
                    ),
                )
            else:
                sessions = [session_id] if session_id else []
                memories = [memory_id] if memory_id else []
                conn.execute(
                    """
                    INSERT INTO entity_aggregates (
                        id, user_id, entity_key, agg_type, value_num, value_unit,
                        item_set, contributing_sessions, contributing_memory_ids,
                        last_updated, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?)
                    """,
                    (
                        agg_id,
                        user_id,
                        entity_key,
                        agg_type,
                        value_delta,
                        value_unit,
                        json.dumps(sessions),
                        json.dumps(memories),
                        now,
                        now,
                    ),
                )

    def upsert_entity_set_member(
        self,
        user_id: str,
        entity_key: str,
        item_value: str,
        session_id: Optional[str] = None,
        memory_id: Optional[str] = None,
    ) -> None:
        """Add a unique item to an item_set aggregate and increment count."""
        agg_id = hashlib.sha256(
            f"{user_id}|item_set|{entity_key}".encode()
        ).hexdigest()
        now = _utcnow_iso()
        with self._get_connection() as conn:
            self._ensure_entity_table(conn)
            existing = conn.execute(
                """
                SELECT value_num, item_set, contributing_sessions,
                       contributing_memory_ids
                FROM entity_aggregates WHERE id = ?
                """,
                (agg_id,),
            ).fetchone()
            if existing:
                items = self._parse_json_value(existing["item_set"], [])
                sessions = self._parse_json_value(
                    existing["contributing_sessions"], []
                )
                memories = self._parse_json_value(
                    existing["contributing_memory_ids"], []
                )
                if item_value not in items:
                    items.append(item_value)
                if session_id and session_id not in sessions:
                    sessions.append(session_id)
                if memory_id and memory_id not in memories:
                    memories.append(memory_id)
                conn.execute(
                    """
                    UPDATE entity_aggregates
                    SET value_num = ?, item_set = ?,
                        contributing_sessions = ?, contributing_memory_ids = ?,
                        last_updated = ?
                    WHERE id = ?
                    """,
                    (
                        len(items),
                        json.dumps(items),
                        json.dumps(sessions),
                        json.dumps(memories),
                        now,
                        agg_id,
                    ),
                )
            else:
                sessions = [session_id] if session_id else []
                memories = [memory_id] if memory_id else []
                conn.execute(
                    """
                    INSERT INTO entity_aggregates (
                        id, user_id, entity_key, agg_type, value_num,
                        value_unit, item_set, contributing_sessions,
                        contributing_memory_ids, last_updated, created_at
                    ) VALUES (?, ?, ?, 'item_set', 1, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        agg_id,
                        user_id,
                        entity_key,
                        json.dumps([item_value]),
                        json.dumps(sessions),
                        json.dumps(memories),
                        now,
                        now,
                    ),
                )

    def get_entity_aggregates(
        self,
        user_id: str,
        agg_type: Optional[str] = None,
        entity_hints: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Query entity aggregates with optional fuzzy match on entity_key."""
        with self._get_connection() as conn:
            self._ensure_entity_table(conn)
            if agg_type and entity_hints:
                conditions = " OR ".join(
                    ["entity_key LIKE ?" for _ in entity_hints]
                )
                params: List[Any] = [user_id, agg_type] + [
                    f"%{hint}%"
                    for hint in entity_hints
                ]
                rows = conn.execute(
                    f"""
                    SELECT * FROM entity_aggregates
                    WHERE user_id = ? AND agg_type = ? AND ({conditions})
                    """,
                    params,
                ).fetchall()
            elif agg_type:
                rows = conn.execute(
                    """
                    SELECT * FROM entity_aggregates
                    WHERE user_id = ? AND agg_type = ?
                    """,
                    (user_id, agg_type),
                ).fetchall()
            elif entity_hints:
                conditions = " OR ".join(
                    ["entity_key LIKE ?" for _ in entity_hints]
                )
                params = [user_id] + [f"%{hint}%" for hint in entity_hints]
                rows = conn.execute(
                    f"""
                    SELECT * FROM entity_aggregates
                    WHERE user_id = ? AND ({conditions})
                    """,
                    params,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM entity_aggregates WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
            return [dict(row) for row in rows]

    def delete_entity_aggregates_for_user(self, user_id: str) -> int:
        """Delete all entity aggregates for a user."""
        with self._get_connection() as conn:
            self._ensure_entity_table(conn)
            cursor = conn.execute(
                "DELETE FROM entity_aggregates WHERE user_id = ?",
                (user_id,),
            )
            return int(cursor.rowcount or 0)
