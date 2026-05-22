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
            if key in {
                "participants", "memory_ids", "embedding",
                "next_possible_moves_json", "consolidated_card_json",
            }:
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

    def upsert_series(self, data: Dict[str, Any]) -> Dict[str, Any]:
        series_id = data.get("id") or str(uuid.uuid4())
        now = _utcnow_iso()
        row = {
            "id": series_id,
            "user_id": data.get("user_id", "default"),
            "namespace": data.get("namespace", "personal"),
            "title": data.get("title", ""),
            "theme": data.get("theme", ""),
            "ultimate_goal": data.get("ultimate_goal"),
            "hero_identity": data.get("hero_identity"),
            "purpose": data.get("purpose", ""),
            "desired_identity": data.get("desired_identity"),
            "core_values_json": json.dumps(data.get("core_values", data.get("core_values_json", []))),
            "long_term_conflicts_json": json.dumps(data.get("long_term_conflicts", data.get("long_term_conflicts_json", []))),
            "current_active_season": data.get("current_active_season"),
            "arc_summary": data.get("arc_summary", ""),
            "active_tensions_json": json.dumps(data.get("active_tensions", data.get("active_tensions_json", []))),
            "latest_season_signal": data.get("latest_season_signal"),
            "deterministic_rollup_json": json.dumps(data.get("deterministic_rollup", data.get("deterministic_rollup_json", {}))),
            "llm_rollup_json": json.dumps(data.get("llm_rollup", data.get("llm_rollup_json", {}))),
            "rollup_model": data.get("rollup_model"),
            "rollup_prompt_version": data.get("rollup_prompt_version"),
            "rollup_source_scene_card_ids_json": json.dumps(data.get("rollup_source_scene_card_ids", data.get("rollup_source_scene_card_ids_json", []))),
            "rollup_input_hash": data.get("rollup_input_hash"),
            "status": data.get("status", "active"),
            "confidence": float(data.get("confidence", 0.5)),
            "created_at": data.get("created_at", now),
            "updated_at": data.get("updated_at", now),
        }
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO series (
                    id, user_id, namespace, title, theme, ultimate_goal,
                    hero_identity, purpose, desired_identity, core_values_json,
                    long_term_conflicts_json, current_active_season, arc_summary,
                    active_tensions_json, latest_season_signal,
                    deterministic_rollup_json, llm_rollup_json, rollup_model,
                    rollup_prompt_version, rollup_source_scene_card_ids_json,
                    rollup_input_hash, status,
                    confidence, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :namespace, :title, :theme, :ultimate_goal,
                    :hero_identity, :purpose, :desired_identity,
                    :core_values_json, :long_term_conflicts_json,
                    :current_active_season, :arc_summary,
                    :active_tensions_json, :latest_season_signal,
                    :deterministic_rollup_json, :llm_rollup_json,
                    :rollup_model, :rollup_prompt_version,
                    :rollup_source_scene_card_ids_json, :rollup_input_hash,
                    :status, :confidence,
                    :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    theme = excluded.theme,
                    ultimate_goal = excluded.ultimate_goal,
                    hero_identity = excluded.hero_identity,
                    purpose = excluded.purpose,
                    desired_identity = excluded.desired_identity,
                    core_values_json = excluded.core_values_json,
                    long_term_conflicts_json = excluded.long_term_conflicts_json,
                    current_active_season = excluded.current_active_season,
                    arc_summary = excluded.arc_summary,
                    active_tensions_json = excluded.active_tensions_json,
                    latest_season_signal = excluded.latest_season_signal,
                    deterministic_rollup_json = excluded.deterministic_rollup_json,
                    llm_rollup_json = excluded.llm_rollup_json,
                    rollup_model = excluded.rollup_model,
                    rollup_prompt_version = excluded.rollup_prompt_version,
                    rollup_source_scene_card_ids_json = excluded.rollup_source_scene_card_ids_json,
                    rollup_input_hash = excluded.rollup_input_hash,
                    status = excluded.status,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                row,
            )
        return self.get_series(series_id) or row

    def get_series(self, series_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
        return self._series_row_to_dict(row) if row else None

    def get_active_series(
        self,
        user_id: str = "default",
        namespace: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM series WHERE user_id = ? AND status = 'active'"
        params: List[Any] = [user_id]
        if namespace:
            query += " AND namespace IN (?, 'personal')"
            params.append(namespace)
        query += " ORDER BY updated_at DESC LIMIT 1"
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
        return self._series_row_to_dict(row) if row else None

    def update_series(self, series_id: str, updates: Dict[str, Any]) -> bool:
        allowed = {
            "title", "theme", "ultimate_goal", "hero_identity", "purpose",
            "desired_identity", "current_active_season", "arc_summary",
            "latest_season_signal", "rollup_model", "rollup_prompt_version",
            "rollup_input_hash", "status", "confidence", "updated_at",
        }
        set_clauses = []
        params: List[Any] = []
        data = dict(updates or {})
        data.setdefault("updated_at", _utcnow_iso())
        for key, value in data.items():
            if key == "core_values":
                set_clauses.append("core_values_json = ?")
                params.append(json.dumps(value))
            elif key == "long_term_conflicts":
                set_clauses.append("long_term_conflicts_json = ?")
                params.append(json.dumps(value))
            elif key == "active_tensions":
                set_clauses.append("active_tensions_json = ?")
                params.append(json.dumps(value))
            elif key == "deterministic_rollup":
                set_clauses.append("deterministic_rollup_json = ?")
                params.append(json.dumps(value))
            elif key == "llm_rollup":
                set_clauses.append("llm_rollup_json = ?")
                params.append(json.dumps(value))
            elif key == "rollup_source_scene_card_ids":
                set_clauses.append("rollup_source_scene_card_ids_json = ?")
                params.append(json.dumps(value))
            elif key in allowed:
                set_clauses.append(f"{key} = ?")
                params.append(value)
            else:
                raise ValueError(f"Invalid series column: {key!r}")
        if not set_clauses:
            return False
        params.append(series_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE series SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
        return True

    def upsert_season(self, data: Dict[str, Any]) -> Dict[str, Any]:
        season_id = data.get("id") or str(uuid.uuid4())
        now = _utcnow_iso()
        row = {
            "id": season_id,
            "series_id": data.get("series_id"),
            "user_id": data.get("user_id", "default"),
            "namespace": data.get("namespace", "default"),
            "title": data.get("title", ""),
            "theme": data.get("theme", ""),
            "major_goal": data.get("major_goal"),
            "dominant_struggle": data.get("dominant_struggle"),
            "transformation_expected": data.get("transformation_expected"),
            "open_threads_json": json.dumps(data.get("open_threads", data.get("open_threads_json", []))),
            "arc_summary": data.get("arc_summary", ""),
            "deterministic_rollup_json": json.dumps(data.get("deterministic_rollup", data.get("deterministic_rollup_json", {}))),
            "llm_rollup_json": json.dumps(data.get("llm_rollup", data.get("llm_rollup_json", {}))),
            "rollup_model": data.get("rollup_model"),
            "rollup_prompt_version": data.get("rollup_prompt_version"),
            "rollup_source_scene_card_ids_json": json.dumps(data.get("rollup_source_scene_card_ids", data.get("rollup_source_scene_card_ids_json", []))),
            "rollup_input_hash": data.get("rollup_input_hash"),
            "period_start": data.get("period_start"),
            "period_end": data.get("period_end"),
            "status": data.get("status", "active"),
            "confidence": float(data.get("confidence", 0.5)),
            "created_at": data.get("created_at", now),
            "updated_at": data.get("updated_at", now),
        }
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO seasons (
                    id, series_id, user_id, namespace, title, theme, major_goal,
                    dominant_struggle, transformation_expected,
                    open_threads_json, arc_summary, period_start, period_end,
                    deterministic_rollup_json, llm_rollup_json, rollup_model,
                    rollup_prompt_version, rollup_source_scene_card_ids_json,
                    rollup_input_hash, status, confidence, created_at, updated_at
                ) VALUES (
                    :id, :series_id, :user_id, :namespace, :title, :theme,
                    :major_goal, :dominant_struggle, :transformation_expected,
                    :open_threads_json, :arc_summary, :period_start,
                    :period_end, :deterministic_rollup_json, :llm_rollup_json,
                    :rollup_model, :rollup_prompt_version,
                    :rollup_source_scene_card_ids_json, :rollup_input_hash,
                    :status, :confidence, :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    theme = excluded.theme,
                    major_goal = excluded.major_goal,
                    dominant_struggle = excluded.dominant_struggle,
                    transformation_expected = excluded.transformation_expected,
                    open_threads_json = excluded.open_threads_json,
                    arc_summary = excluded.arc_summary,
                    deterministic_rollup_json = excluded.deterministic_rollup_json,
                    llm_rollup_json = excluded.llm_rollup_json,
                    rollup_model = excluded.rollup_model,
                    rollup_prompt_version = excluded.rollup_prompt_version,
                    rollup_source_scene_card_ids_json = excluded.rollup_source_scene_card_ids_json,
                    rollup_input_hash = excluded.rollup_input_hash,
                    period_end = excluded.period_end,
                    status = excluded.status,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                row,
            )
        return self.get_season(season_id) or row

    def get_season(self, season_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)).fetchone()
        return self._season_row_to_dict(row) if row else None

    def get_active_season(
        self,
        series_id: str,
        user_id: str = "default",
        namespace: str = "default",
    ) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM seasons
                WHERE series_id = ? AND user_id = ? AND namespace = ?
                  AND status = 'active'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (series_id, user_id, namespace),
            ).fetchone()
        return self._season_row_to_dict(row) if row else None

    def update_season(self, season_id: str, updates: Dict[str, Any]) -> bool:
        allowed = {
            "title", "theme", "major_goal", "dominant_struggle",
            "transformation_expected", "arc_summary", "period_start",
            "period_end", "rollup_model", "rollup_prompt_version",
            "rollup_input_hash", "status", "confidence", "updated_at",
        }
        set_clauses = []
        params: List[Any] = []
        data = dict(updates or {})
        data.setdefault("updated_at", _utcnow_iso())
        for key, value in data.items():
            if key == "open_threads":
                set_clauses.append("open_threads_json = ?")
                params.append(json.dumps(value))
            elif key == "deterministic_rollup":
                set_clauses.append("deterministic_rollup_json = ?")
                params.append(json.dumps(value))
            elif key == "llm_rollup":
                set_clauses.append("llm_rollup_json = ?")
                params.append(json.dumps(value))
            elif key == "rollup_source_scene_card_ids":
                set_clauses.append("rollup_source_scene_card_ids_json = ?")
                params.append(json.dumps(value))
            elif key in allowed:
                set_clauses.append(f"{key} = ?")
                params.append(value)
            else:
                raise ValueError(f"Invalid season column: {key!r}")
        if not set_clauses:
            return False
        params.append(season_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE seasons SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
        return True

    def upsert_story_character(self, data: Dict[str, Any]) -> Dict[str, Any]:
        character_id = data.get("id") or str(uuid.uuid4())
        now = _utcnow_iso()
        row = {
            "id": character_id,
            "user_id": data.get("user_id", "default"),
            "namespace": data.get("namespace", "personal"),
            "name": data.get("name", ""),
            "character_type": data.get("character_type", "person"),
            "stable_identity_ref": data.get("stable_identity_ref"),
            "description": data.get("description", ""),
            "skills_json": json.dumps(data.get("skills", data.get("skills_json", []))),
            "influence": float(data.get("influence", 0.5)),
            "trust_level": float(data.get("trust_level", 0.5)),
            "lessons_learned_json": json.dumps(data.get("lessons_learned", data.get("lessons_learned_json", []))),
            "created_at": data.get("created_at", now),
            "updated_at": data.get("updated_at", now),
        }
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO story_characters (
                    id, user_id, namespace, name, character_type,
                    stable_identity_ref, description, skills_json, influence,
                    trust_level, lessons_learned_json, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :namespace, :name, :character_type,
                    :stable_identity_ref, :description, :skills_json,
                    :influence, :trust_level, :lessons_learned_json,
                    :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    skills_json = excluded.skills_json,
                    influence = excluded.influence,
                    trust_level = excluded.trust_level,
                    lessons_learned_json = excluded.lessons_learned_json,
                    updated_at = excluded.updated_at
                """,
                row,
            )
        return self.get_story_character(character_id) or row

    def get_story_character(self, character_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM story_characters WHERE id = ?",
                (character_id,),
            ).fetchone()
        return self._story_character_row_to_dict(row) if row else None

    def get_story_character_by_name(
        self,
        name: str,
        user_id: str = "default",
        namespace: str = "personal",
        character_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM story_characters WHERE user_id = ? AND namespace = ? AND lower(name) = lower(?)"
        params: List[Any] = [user_id, namespace, name]
        if character_type:
            query += " AND character_type = ?"
            params.append(character_type)
        query += " ORDER BY updated_at DESC LIMIT 1"
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
        return self._story_character_row_to_dict(row) if row else None

    def upsert_episode(self, data: Dict[str, Any]) -> Dict[str, Any]:
        episode_id = data.get("id") or str(uuid.uuid4())
        now = _utcnow_iso()
        row = {
            "id": episode_id,
            "series_id": data.get("series_id"),
            "season_id": data.get("season_id"),
            "user_id": data.get("user_id", "default"),
            "local_date": data.get("local_date"),
            "timezone": data.get("timezone", "UTC"),
            "namespace": data.get("namespace", "default"),
            "title": data.get("title", ""),
            "summary": data.get("summary", ""),
            "primary_hero_id": data.get("primary_hero_id"),
            "goal": data.get("goal"),
            "conflict": data.get("conflict"),
            "key_decisions_json": json.dumps(data.get("key_decisions", data.get("key_decisions_json", []))),
            "outcome": data.get("outcome"),
            "lesson": data.get("lesson"),
            "unresolved_threads_json": json.dumps(data.get("unresolved_threads", data.get("unresolved_threads_json", []))),
            "story_progress": data.get("story_progress"),
            "category_summaries_json": json.dumps(data.get("category_summaries", data.get("category_summaries_json", {}))),
            "deterministic_rollup_json": json.dumps(data.get("deterministic_rollup", data.get("deterministic_rollup_json", {}))),
            "llm_rollup_json": json.dumps(data.get("llm_rollup", data.get("llm_rollup_json", {}))),
            "rollup_model": data.get("rollup_model"),
            "rollup_prompt_version": data.get("rollup_prompt_version"),
            "rollup_source_scene_card_ids_json": json.dumps(data.get("rollup_source_scene_card_ids", data.get("rollup_source_scene_card_ids_json", []))),
            "rollup_input_hash": data.get("rollup_input_hash"),
            "agent_ids_json": json.dumps(data.get("agent_ids", data.get("agent_ids_json", []))),
            "scene_ids_json": json.dumps(data.get("scene_ids", data.get("scene_ids_json", []))),
            "open_loops_json": json.dumps(data.get("open_loops", data.get("open_loops_json", []))),
            "status": data.get("status", "open"),
            "created_at": data.get("created_at", now),
            "updated_at": data.get("updated_at", now),
        }
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    id, series_id, season_id, user_id, local_date, timezone,
                    namespace, title, summary, primary_hero_id, goal, conflict,
                    key_decisions_json, outcome, lesson,
                    unresolved_threads_json, story_progress,
                    category_summaries_json, deterministic_rollup_json,
                    llm_rollup_json, rollup_model, rollup_prompt_version,
                    rollup_source_scene_card_ids_json, rollup_input_hash,
                    agent_ids_json, scene_ids_json, open_loops_json,
                    status, created_at, updated_at
                ) VALUES (
                    :id, :series_id, :season_id, :user_id, :local_date,
                    :timezone, :namespace, :title, :summary, :primary_hero_id,
                    :goal, :conflict, :key_decisions_json, :outcome, :lesson,
                    :unresolved_threads_json, :story_progress,
                    :category_summaries_json, :deterministic_rollup_json,
                    :llm_rollup_json, :rollup_model, :rollup_prompt_version,
                    :rollup_source_scene_card_ids_json, :rollup_input_hash,
                    :agent_ids_json,
                    :scene_ids_json, :open_loops_json, :status,
                    :created_at, :updated_at
                )
                ON CONFLICT(user_id, namespace, local_date, timezone) DO UPDATE SET
                    series_id = COALESCE(excluded.series_id, episodes.series_id),
                    season_id = COALESCE(excluded.season_id, episodes.season_id),
                    title = excluded.title,
                    summary = excluded.summary,
                    primary_hero_id = COALESCE(excluded.primary_hero_id, episodes.primary_hero_id),
                    goal = COALESCE(excluded.goal, episodes.goal),
                    conflict = COALESCE(excluded.conflict, episodes.conflict),
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                row,
            )
            saved = conn.execute(
                """
                SELECT * FROM episodes
                WHERE user_id = ? AND namespace = ? AND local_date = ? AND timezone = ?
                """,
                (row["user_id"], row["namespace"], row["local_date"], row["timezone"]),
            ).fetchone()
        return self._episode_row_to_dict(saved) if saved else row

    def get_episode(self, episode_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        return self._episode_row_to_dict(row) if row else None

    def get_episode_for_day(
        self,
        user_id: str,
        namespace: str,
        local_date: str,
        timezone: str,
    ) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM episodes
                WHERE user_id = ? AND namespace = ? AND local_date = ? AND timezone = ?
                """,
                (user_id, namespace, local_date, timezone),
            ).fetchone()
        return self._episode_row_to_dict(row) if row else None

    def update_episode(self, episode_id: str, updates: Dict[str, Any]) -> bool:
        json_fields = {
            "key_decisions": "key_decisions_json",
            "unresolved_threads": "unresolved_threads_json",
            "category_summaries": "category_summaries_json",
            "deterministic_rollup": "deterministic_rollup_json",
            "llm_rollup": "llm_rollup_json",
            "rollup_source_scene_card_ids": "rollup_source_scene_card_ids_json",
            "agent_ids": "agent_ids_json",
            "scene_ids": "scene_ids_json",
            "open_loops": "open_loops_json",
        }
        allowed = {
            "series_id", "season_id", "title", "summary", "primary_hero_id",
            "goal", "conflict", "outcome", "lesson", "story_progress",
            "rollup_model", "rollup_prompt_version", "rollup_input_hash",
            "status", "updated_at",
        }
        set_clauses = []
        params: List[Any] = []
        data = dict(updates or {})
        data.setdefault("updated_at", _utcnow_iso())
        for key, value in data.items():
            if key in json_fields:
                set_clauses.append(f"{json_fields[key]} = ?")
                params.append(json.dumps(value))
            elif key in allowed:
                set_clauses.append(f"{key} = ?")
                params.append(value)
            else:
                raise ValueError(f"Invalid episode column: {key!r}")
        if not set_clauses:
            return False
        params.append(episode_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE episodes SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
        return True

    def upsert_episode_character(self, data: Dict[str, Any]) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episode_characters (
                    episode_id, character_id, role, relationship_to_hero,
                    salience, evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("episode_id"),
                    data.get("character_id"),
                    data.get("role", "ally"),
                    data.get("relationship_to_hero"),
                    float(data.get("salience", 0.5)),
                    json.dumps(data.get("evidence_refs", data.get("evidence_refs_json", []))),
                ),
            )

    def upsert_scene_character(self, data: Dict[str, Any]) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scene_characters (
                    scene_id, character_id, role, contribution, salience,
                    evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("scene_id"),
                    data.get("character_id"),
                    data.get("role", "ally"),
                    data.get("contribution", ""),
                    float(data.get("salience", 0.5)),
                    json.dumps(data.get("evidence_refs", data.get("evidence_refs_json", []))),
                ),
            )

    def add_scene_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        event_id = data.get("id") or str(uuid.uuid4())
        now = _utcnow_iso()
        row = {
            "id": event_id,
            "scene_id": data.get("scene_id"),
            "event_type": data.get("event_type", "observation"),
            "summary": data.get("summary", ""),
            "evidence_ref": data.get("evidence_ref"),
            "metadata_json": json.dumps(data.get("metadata", data.get("metadata_json", {}))),
            "created_at": data.get("created_at", now),
        }
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO scene_events (
                    id, scene_id, event_type, summary, evidence_ref,
                    metadata_json, created_at
                ) VALUES (
                    :id, :scene_id, :event_type, :summary, :evidence_ref,
                    :metadata_json, :created_at
                )
                """,
                row,
            )
        return self.get_scene_event(event_id) or row

    def get_scene_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM scene_events WHERE id = ?", (event_id,)).fetchone()
        return self._scene_event_row_to_dict(row) if row else None

    def get_scene_events(self, scene_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scene_events WHERE scene_id = ? ORDER BY created_at",
                (scene_id,),
            ).fetchall()
        return [self._scene_event_row_to_dict(row) for row in rows]

    def replace_scene_categories(
        self,
        scene_id: str,
        categories: List[str],
        source: str = "explicit",
    ) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM scene_categories WHERE scene_id = ?", (scene_id,))
            conn.executemany(
                """
                INSERT OR REPLACE INTO scene_categories
                (scene_id, category_id, weight, source) VALUES (?, ?, ?, ?)
                """,
                [(scene_id, str(cat), 1.0, source) for cat in categories if str(cat).strip()],
            )

    def replace_scene_markers(
        self,
        scene_id: str,
        markers: Dict[str, Any],
        source: str = "explicit",
    ) -> None:
        rows = []
        for key, value in (markers or {}).items():
            if value is None:
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                rows.append((scene_id, str(key), str(item), 1.0, source))
        with self._get_connection() as conn:
            conn.execute("DELETE FROM scene_markers WHERE scene_id = ?", (scene_id,))
            conn.executemany(
                """
                INSERT OR REPLACE INTO scene_markers
                (scene_id, marker_key, marker_value, weight, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_scene_categories(self, scene_id: str) -> List[str]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT category_id FROM scene_categories WHERE scene_id = ? ORDER BY weight DESC",
                (scene_id,),
            ).fetchall()
        return [str(row["category_id"]) for row in rows]

    def get_scene_markers(self, scene_id: str) -> Dict[str, List[str]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT marker_key, marker_value FROM scene_markers WHERE scene_id = ?",
                (scene_id,),
            ).fetchall()
        markers: Dict[str, List[str]] = {}
        for row in rows:
            markers.setdefault(str(row["marker_key"]), []).append(str(row["marker_value"]))
        return markers

    def upsert_scene_card(self, data: Dict[str, Any]) -> Dict[str, Any]:
        card_id = data.get("id") or str(uuid.uuid4())
        now = _utcnow_iso()
        row = {
            "id": card_id,
            "scene_id": data.get("scene_id"),
            "episode_id": data.get("episode_id"),
            "user_id": data.get("user_id", "default"),
            "agent_id": data.get("agent_id"),
            "agent_category": data.get("agent_category"),
            "namespace": data.get("namespace", "default"),
            "schema_version": data.get("schema_version", "dhee.scene_card.v1"),
            "summary": data.get("summary", ""),
            "staleness_policy": data.get("staleness_policy", "valid_until_superseded"),
            "importance": float(data.get("importance", 0.5)),
            "confidence": float(data.get("confidence", 0.5)),
            "reuse_policy": data.get("reuse_policy", "private"),
            "visibility_scope": data.get("visibility_scope", "private"),
            "privacy_class": data.get("privacy_class", "user_private"),
            "retrieval_tags_json": json.dumps(data.get("retrieval_tags", data.get("retrieval_tags_json", []))),
            "evidence_refs_json": json.dumps(data.get("evidence_refs", data.get("evidence_refs_json", []))),
            "do_not_use_for_json": json.dumps(data.get("do_not_use_for", data.get("do_not_use_for_json", []))),
            "durable_facts_json": json.dumps(data.get("durable_facts", data.get("durable_facts_json", []))),
            "decisions_json": json.dumps(data.get("decisions", data.get("decisions_json", []))),
            "procedures_json": json.dumps(data.get("procedures", data.get("procedures_json", []))),
            "success_patterns_json": json.dumps(data.get("success_patterns", data.get("success_patterns_json", []))),
            "failure_patterns_json": json.dumps(data.get("failure_patterns", data.get("failure_patterns_json", []))),
            "open_loops_json": json.dumps(data.get("open_loops", data.get("open_loops_json", []))),
            "entities_json": json.dumps(data.get("entities", data.get("entities_json", []))),
            "artifacts_json": json.dumps(data.get("artifacts", data.get("artifacts_json", []))),
            "created_at": data.get("created_at", now),
            "updated_at": data.get("updated_at", now),
        }
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO scene_cards (
                    id, scene_id, episode_id, user_id, agent_id, agent_category,
                    namespace, schema_version, summary, staleness_policy,
                    importance, confidence, reuse_policy, visibility_scope,
                    privacy_class, retrieval_tags_json, evidence_refs_json,
                    do_not_use_for_json, durable_facts_json, decisions_json,
                    procedures_json, success_patterns_json,
                    failure_patterns_json, open_loops_json, entities_json,
                    artifacts_json, created_at, updated_at
                ) VALUES (
                    :id, :scene_id, :episode_id, :user_id, :agent_id,
                    :agent_category, :namespace, :schema_version, :summary,
                    :staleness_policy, :importance, :confidence,
                    :reuse_policy, :visibility_scope, :privacy_class,
                    :retrieval_tags_json, :evidence_refs_json,
                    :do_not_use_for_json, :durable_facts_json,
                    :decisions_json, :procedures_json,
                    :success_patterns_json, :failure_patterns_json,
                    :open_loops_json, :entities_json, :artifacts_json,
                    :created_at, :updated_at
                )
                ON CONFLICT(scene_id) DO UPDATE SET
                    summary = excluded.summary,
                    importance = excluded.importance,
                    confidence = excluded.confidence,
                    reuse_policy = excluded.reuse_policy,
                    visibility_scope = excluded.visibility_scope,
                    privacy_class = excluded.privacy_class,
                    retrieval_tags_json = excluded.retrieval_tags_json,
                    evidence_refs_json = excluded.evidence_refs_json,
                    do_not_use_for_json = excluded.do_not_use_for_json,
                    durable_facts_json = excluded.durable_facts_json,
                    decisions_json = excluded.decisions_json,
                    procedures_json = excluded.procedures_json,
                    success_patterns_json = excluded.success_patterns_json,
                    failure_patterns_json = excluded.failure_patterns_json,
                    open_loops_json = excluded.open_loops_json,
                    entities_json = excluded.entities_json,
                    artifacts_json = excluded.artifacts_json,
                    updated_at = excluded.updated_at
                """,
                row,
            )
            saved = conn.execute("SELECT * FROM scene_cards WHERE scene_id = ?", (row["scene_id"],)).fetchone()
        return self._scene_card_row_to_dict(saved) if saved else row

    def get_scene_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM scene_cards WHERE id = ?", (card_id,)).fetchone()
        return self._scene_card_row_to_dict(row) if row else None

    def get_scene_card_by_scene(self, scene_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM scene_cards WHERE scene_id = ?", (scene_id,)).fetchone()
        return self._scene_card_row_to_dict(row) if row else None

    def list_scene_cards(
        self,
        user_id: str = "default",
        namespace: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM scene_cards WHERE user_id = ?"
        params: List[Any] = [user_id]
        if namespace:
            query += " AND namespace = ?"
            params.append(namespace)
        query += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._scene_card_row_to_dict(row) for row in rows]

    def add_scene_card_claim(self, data: Dict[str, Any]) -> Dict[str, Any]:
        claim_id = data.get("id") or str(uuid.uuid4())
        now = _utcnow_iso()
        row = {
            "id": claim_id,
            "scene_card_id": data.get("scene_card_id"),
            "kind": data.get("kind", "note"),
            "claim": data.get("claim", ""),
            "confidence": float(data.get("confidence", 0.5)),
            "valid_until": data.get("valid_until"),
            "supersedes_json": json.dumps(data.get("supersedes", data.get("supersedes_json", []))),
            "contradicted_by_json": json.dumps(data.get("contradicted_by", data.get("contradicted_by_json", []))),
            "evidence_refs_json": json.dumps(data.get("evidence_refs", data.get("evidence_refs_json", []))),
            "created_at": data.get("created_at", now),
        }
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scene_card_claims (
                    id, scene_card_id, kind, claim, confidence, valid_until,
                    supersedes_json, contradicted_by_json, evidence_refs_json,
                    created_at
                ) VALUES (
                    :id, :scene_card_id, :kind, :claim, :confidence,
                    :valid_until, :supersedes_json, :contradicted_by_json,
                    :evidence_refs_json, :created_at
                )
                """,
                row,
            )
        return row

    def add_scene_edge(
        self,
        from_scene_id: str,
        to_scene_id: str,
        edge_type: str,
        created_at: Optional[str] = None,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scene_edges
                (from_scene_id, to_scene_id, edge_type, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (from_scene_id, to_scene_id, edge_type, created_at or _utcnow_iso()),
            )

    def scene_has_blocking_edge(self, scene_id: str) -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM scene_edges
                WHERE to_scene_id = ? AND edge_type IN ('supersedes', 'contradicts')
                LIMIT 1
                """,
                (scene_id,),
            ).fetchone()
        return row is not None

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
        for key, default in (
            ("next_possible_moves_json", []),
            ("consolidated_card_json", {}),
        ):
            if key in data:
                data[key] = self._parse_json_value(data.get(key), default)
        data["tombstone"] = bool(data.get("tombstone", 0))
        return data

    def _series_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["core_values"] = self._parse_json_value(data.pop("core_values_json", None), [])
        data["long_term_conflicts"] = self._parse_json_value(
            data.pop("long_term_conflicts_json", None), []
        )
        data["active_tensions"] = self._parse_json_value(
            data.pop("active_tensions_json", None), []
        )
        data["deterministic_rollup"] = self._parse_json_value(
            data.pop("deterministic_rollup_json", None), {}
        )
        data["llm_rollup"] = self._parse_json_value(data.pop("llm_rollup_json", None), {})
        data["rollup_source_scene_card_ids"] = self._parse_json_value(
            data.pop("rollup_source_scene_card_ids_json", None), []
        )
        return data

    def _season_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["open_threads"] = self._parse_json_value(data.pop("open_threads_json", None), [])
        data["deterministic_rollup"] = self._parse_json_value(
            data.pop("deterministic_rollup_json", None), {}
        )
        data["llm_rollup"] = self._parse_json_value(data.pop("llm_rollup_json", None), {})
        data["rollup_source_scene_card_ids"] = self._parse_json_value(
            data.pop("rollup_source_scene_card_ids_json", None), []
        )
        return data

    def _story_character_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["skills"] = self._parse_json_value(data.pop("skills_json", None), [])
        data["lessons_learned"] = self._parse_json_value(
            data.pop("lessons_learned_json", None), []
        )
        return data

    def _episode_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        for json_key, public_key, default in (
            ("key_decisions_json", "key_decisions", []),
            ("unresolved_threads_json", "unresolved_threads", []),
            ("category_summaries_json", "category_summaries", {}),
            ("deterministic_rollup_json", "deterministic_rollup", {}),
            ("llm_rollup_json", "llm_rollup", {}),
            ("rollup_source_scene_card_ids_json", "rollup_source_scene_card_ids", []),
            ("agent_ids_json", "agent_ids", []),
            ("scene_ids_json", "scene_ids", []),
            ("open_loops_json", "open_loops", []),
        ):
            data[public_key] = self._parse_json_value(data.pop(json_key, None), default)
        return data

    def _scene_event_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["metadata"] = self._parse_json_value(data.pop("metadata_json", None), {})
        return data

    def _scene_card_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        for json_key, public_key, default in (
            ("retrieval_tags_json", "retrieval_tags", []),
            ("evidence_refs_json", "evidence_refs", []),
            ("do_not_use_for_json", "do_not_use_for", []),
            ("durable_facts_json", "durable_facts", []),
            ("decisions_json", "decisions", []),
            ("procedures_json", "procedures", []),
            ("success_patterns_json", "success_patterns", []),
            ("failure_patterns_json", "failure_patterns", []),
            ("open_loops_json", "open_loops", []),
            ("entities_json", "entities", []),
            ("artifacts_json", "artifacts", []),
        ):
            data[public_key] = self._parse_json_value(data.pop(json_key, None), default)
        data["categories"] = self.get_scene_categories(data["scene_id"])
        data["markers"] = self.get_scene_markers(data["scene_id"])
        scene = self.get_scene(data["scene_id"])
        if scene:
            data["scene_outcome_status"] = scene.get("outcome_status")
            data["scene_action_lane"] = scene.get("action_lane")
        episode = self.get_episode(data["episode_id"]) if data.get("episode_id") else None
        if episode:
            data["series_id"] = episode.get("series_id")
            data["season_id"] = episode.get("season_id")
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
