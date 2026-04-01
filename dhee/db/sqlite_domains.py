import json
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from .sqlite_common import VALID_PROFILE_COLUMNS, VALID_SCENE_COLUMNS, _utcnow_iso


class SQLiteDomainMixin:
    """Category, scene, and profile storage APIs for FullSQLiteManager."""

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
                    json.dumps(category_data.get("embedding"))
                    if category_data.get("embedding")
                    else None,
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
                (category_id,),
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
            rows.append(
                (
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
                    json.dumps(cat.get("embedding"))
                    if cat.get("embedding")
                    else None,
                    json.dumps(cat.get("keywords", [])),
                    cat.get("summary"),
                    cat.get("summary_updated_at"),
                    json.dumps(cat.get("related_ids", [])),
                    cat.get("strength", 1.0),
                )
            )
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
        data["children_ids"] = self._parse_json_value(
            data.get("children_ids"), []
        )
        data["keywords"] = self._parse_json_value(data.get("keywords"), [])
        data["related_ids"] = self._parse_json_value(
            data.get("related_ids"), []
        )
        data["embedding"] = self._parse_json_value(data.get("embedding"), None)
        return data

    def _migrate_add_column(self, table: str, column: str, col_type: str) -> None:
        """Add a column to an existing table if it doesn't already exist."""
        with self._get_connection() as conn:
            self._migrate_add_column_conn(conn, table, column, col_type)

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
                    json.dumps(scene_data.get("embedding"))
                    if scene_data.get("embedding")
                    else None,
                    scene_data.get("strength", 1.0),
                    scene_data.get("access_count", 0),
                    1 if scene_data.get("tombstone", False) else 0,
                    scene_data.get("layer", "sml"),
                    scene_data.get(
                        "scene_strength", scene_data.get("strength", 1.0)
                    ),
                    scene_data.get("topic_embedding_ref"),
                    scene_data.get("namespace", "default"),
                ),
            )
        return scene_id

    def get_scene(self, scene_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM scenes WHERE id = ? AND tombstone = 0",
                (scene_id,),
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

    def add_scene_memory(
        self,
        scene_id: str,
        memory_id: str,
        position: int = 0,
    ) -> None:
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
        data["participants"] = self._parse_json_value(
            data.get("participants"), []
        )
        data["memory_ids"] = self._parse_json_value(data.get("memory_ids"), [])
        data["embedding"] = self._parse_json_value(data.get("embedding"), None)
        data["tombstone"] = bool(data.get("tombstone", 0))
        return data

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
                    json.dumps(profile_data.get("embedding"))
                    if profile_data.get("embedding")
                    else None,
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
                "SELECT * FROM profiles WHERE id = ?",
                (profile_id,),
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
            if key in {
                "facts",
                "preferences",
                "relationships",
                "aliases",
                "theory_of_mind",
                "embedding",
            }:
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

    def get_all_profiles(
        self,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM profiles"
        params: List[Any] = []
        if user_id:
            query += " WHERE user_id = ?"
            params.append(user_id)
        query += " ORDER BY strength DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._profile_row_to_dict(row) for row in rows]

    def get_profile_by_name(
        self,
        name: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Find a profile by exact name match, then fall back to alias scan."""
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
            alias_query = "SELECT * FROM profiles WHERE aliases LIKE ?"
            alias_params: List[Any] = [f'%"{name}"%']
            if user_id:
                alias_query += " AND user_id = ?"
                alias_params.append(user_id)
            alias_query += " LIMIT 1"
            row = conn.execute(alias_query, alias_params).fetchone()
            if row:
                result = self._profile_row_to_dict(row)
                if name.lower() in [a.lower() for a in result.get("aliases", [])]:
                    return result
        return None

    def find_profile_by_substring(
        self,
        name: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Find a profile where the name contains the query as a substring."""
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

    def add_profile_memory(
        self,
        profile_id: str,
        memory_id: str,
        role: str = "mentioned",
    ) -> None:
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
        data["facts"] = self._parse_json_value(data.get("facts"), [])
        data["preferences"] = self._parse_json_value(
            data.get("preferences"), []
        )
        data["relationships"] = self._parse_json_value(
            data.get("relationships"), []
        )
        data["aliases"] = self._parse_json_value(data.get("aliases"), [])
        data["theory_of_mind"] = self._parse_json_value(
            data.get("theory_of_mind"), {}
        )
        data["embedding"] = self._parse_json_value(data.get("embedding"), None)
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
