import json
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from .sqlite_common import _utcnow_iso


class SQLiteArtifactMixin:
    """Artifact storage APIs for host-extracted file knowledge."""

    _ARTIFACT_STATES = ("attached", "parsed_by_host", "indexed", "portable")

    def _ensure_artifact_tables(self, conn: sqlite3.Connection) -> None:
        if self._is_migration_applied(conn, "v4_artifact_store"):
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifact_assets (
                artifact_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT,
                byte_size INTEGER,
                lifecycle_state TEXT NOT NULL DEFAULT 'attached'
                    CHECK (lifecycle_state IN ('attached', 'parsed_by_host', 'indexed', 'portable')),
                attached_at TEXT,
                parsed_at TEXT,
                indexed_at TEXT,
                portable_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}',
                UNIQUE(user_id, content_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_artifact_assets_user_updated
                ON artifact_assets(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_artifact_assets_filename
                ON artifact_assets(filename);

            CREATE TABLE IF NOT EXISTS artifact_bindings (
                id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL REFERENCES artifact_assets(artifact_id),
                user_id TEXT NOT NULL,
                project_id TEXT,
                workspace_id TEXT,
                folder_path TEXT,
                relative_path TEXT,
                source_path TEXT,
                harness TEXT,
                binding_source TEXT,
                binding_key TEXT NOT NULL UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_artifact_bindings_artifact
                ON artifact_bindings(artifact_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_artifact_bindings_workspace
                ON artifact_bindings(user_id, workspace_id, folder_path);
            CREATE INDEX IF NOT EXISTS idx_artifact_bindings_source
                ON artifact_bindings(source_path);

            CREATE TABLE IF NOT EXISTS artifact_extractions (
                id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL REFERENCES artifact_assets(artifact_id),
                user_id TEXT NOT NULL,
                extraction_source TEXT NOT NULL,
                extraction_version TEXT NOT NULL,
                extracted_text TEXT NOT NULL,
                extracted_text_hash TEXT NOT NULL,
                extraction_timestamp TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(artifact_id, extraction_source, extraction_version, extracted_text_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_artifact_extractions_artifact
                ON artifact_extractions(artifact_id, extraction_timestamp DESC);

            CREATE TABLE IF NOT EXISTS artifact_chunks (
                id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL REFERENCES artifact_assets(artifact_id),
                extraction_id TEXT NOT NULL REFERENCES artifact_extractions(id),
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                start_offset INTEGER DEFAULT 0,
                end_offset INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(extraction_id, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS idx_artifact_chunks_artifact
                ON artifact_chunks(artifact_id, chunk_index);
            CREATE INDEX IF NOT EXISTS idx_artifact_chunks_hash
                ON artifact_chunks(content_hash);
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES ('v4_artifact_store')"
        )

    def _artifact_asset_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["metadata"] = self._parse_json_value(data.get("metadata"), {})
        return data

    def _artifact_binding_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["metadata"] = self._parse_json_value(data.get("metadata"), {})
        return data

    def _artifact_extraction_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["metadata"] = self._parse_json_value(data.get("metadata"), {})
        return data

    def _artifact_chunk_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["metadata"] = self._parse_json_value(data.get("metadata"), {})
        return data

    def get_artifact_by_content_hash(
        self,
        content_hash: str,
        user_id: str = "default",
    ) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM artifact_assets
                WHERE content_hash = ? AND user_id = ?
                LIMIT 1
                """,
                (content_hash, user_id),
            ).fetchone()
            if row:
                return self._artifact_asset_row_to_dict(row)
        return None

    def save_artifact_asset(self, asset_data: Dict[str, Any]) -> str:
        user_id = str(asset_data.get("user_id") or "default")
        content_hash = str(asset_data.get("content_hash") or "").strip()
        if not content_hash:
            raise ValueError("artifact content_hash is required")

        now = _utcnow_iso()
        with self._get_connection() as conn:
            existing = conn.execute(
                """
                SELECT artifact_id, lifecycle_state FROM artifact_assets
                WHERE user_id = ? AND content_hash = ?
                LIMIT 1
                """,
                (user_id, content_hash),
            ).fetchone()
            if existing:
                artifact_id = str(existing["artifact_id"])
                next_state = self._merge_artifact_state(
                    str(existing["lifecycle_state"] or "attached"),
                    str(asset_data.get("lifecycle_state") or "attached"),
                )
                conn.execute(
                    """
                    UPDATE artifact_assets
                    SET filename = ?,
                        mime_type = ?,
                        byte_size = ?,
                        lifecycle_state = ?,
                        attached_at = COALESCE(attached_at, ?),
                        parsed_at = COALESCE(parsed_at, ?),
                        indexed_at = COALESCE(indexed_at, ?),
                        portable_at = COALESCE(portable_at, ?),
                        updated_at = ?,
                        metadata = ?
                    WHERE artifact_id = ?
                    """,
                    (
                        asset_data.get("filename", ""),
                        asset_data.get("mime_type"),
                        asset_data.get("byte_size"),
                        next_state,
                        asset_data.get("attached_at"),
                        asset_data.get("parsed_at"),
                        asset_data.get("indexed_at"),
                        asset_data.get("portable_at"),
                        now,
                        json.dumps(asset_data.get("metadata", {})),
                        artifact_id,
                    ),
                )
                return artifact_id

            artifact_id = str(asset_data.get("artifact_id") or uuid.uuid4())
            conn.execute(
                """
                INSERT INTO artifact_assets (
                    artifact_id, user_id, content_hash, filename, mime_type, byte_size,
                    lifecycle_state, attached_at, parsed_at, indexed_at, portable_at,
                    created_at, updated_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    user_id,
                    content_hash,
                    asset_data.get("filename", ""),
                    asset_data.get("mime_type"),
                    asset_data.get("byte_size"),
                    asset_data.get("lifecycle_state", "attached"),
                    asset_data.get("attached_at"),
                    asset_data.get("parsed_at"),
                    asset_data.get("indexed_at"),
                    asset_data.get("portable_at"),
                    asset_data.get("created_at", now),
                    asset_data.get("updated_at", now),
                    json.dumps(asset_data.get("metadata", {})),
                ),
            )
            return artifact_id

    def update_artifact_asset(self, artifact_id: str, updates: Dict[str, Any]) -> bool:
        if not updates:
            return False
        allowed = {
            "filename",
            "mime_type",
            "byte_size",
            "lifecycle_state",
            "attached_at",
            "parsed_at",
            "indexed_at",
            "portable_at",
            "updated_at",
            "metadata",
        }
        set_clauses: List[str] = []
        params: List[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "metadata":
                value = json.dumps(value or {})
            set_clauses.append(f"{key} = ?")
            params.append(value)
        if not set_clauses:
            return False
        if "updated_at" not in updates:
            set_clauses.append("updated_at = ?")
            params.append(_utcnow_iso())
        params.append(artifact_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE artifact_assets SET {', '.join(set_clauses)} WHERE artifact_id = ?",
                params,
            )
        return True

    def save_artifact_binding(self, binding_data: Dict[str, Any]) -> str:
        binding_key = str(binding_data.get("binding_key") or "").strip()
        if not binding_key:
            raise ValueError("artifact binding_key is required")

        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM artifact_bindings WHERE binding_key = ? LIMIT 1",
                (binding_key,),
            ).fetchone()
            if existing:
                return str(existing["id"])

            binding_id = str(binding_data.get("id") or uuid.uuid4())
            conn.execute(
                """
                INSERT INTO artifact_bindings (
                    id, artifact_id, user_id, project_id, workspace_id, folder_path,
                    relative_path, source_path, harness, binding_source, binding_key,
                    created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    binding_id,
                    binding_data.get("artifact_id"),
                    binding_data.get("user_id", "default"),
                    binding_data.get("project_id"),
                    binding_data.get("workspace_id"),
                    binding_data.get("folder_path"),
                    binding_data.get("relative_path"),
                    binding_data.get("source_path"),
                    binding_data.get("harness"),
                    binding_data.get("binding_source"),
                    binding_key,
                    binding_data.get("created_at", _utcnow_iso()),
                    json.dumps(binding_data.get("metadata", {})),
                ),
            )
            return binding_id

    def get_artifact_bindings(
        self,
        artifact_id: str,
        *,
        workspace_id: Optional[str] = None,
        folder_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM artifact_bindings WHERE artifact_id = ?"
        params: List[Any] = [artifact_id]
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        if folder_path:
            query += " AND folder_path = ?"
            params.append(folder_path)
        query += " ORDER BY created_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._artifact_binding_row_to_dict(row) for row in rows]

    def find_artifact_by_source_path(
        self,
        source_path: str,
        user_id: str = "default",
        *,
        workspace_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        query = """
            SELECT a.*
            FROM artifact_assets a
            JOIN artifact_bindings b ON b.artifact_id = a.artifact_id
            WHERE a.user_id = ? AND b.source_path = ?
        """
        params: List[Any] = [user_id, source_path]
        if workspace_id:
            query += " AND b.workspace_id = ?"
            params.append(workspace_id)
        query += " ORDER BY b.created_at DESC LIMIT 1"
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            if row:
                return self._artifact_asset_row_to_dict(row)
        return None

    def save_artifact_extraction(self, extraction_data: Dict[str, Any]) -> str:
        artifact_id = str(extraction_data.get("artifact_id") or "").strip()
        user_id = str(extraction_data.get("user_id") or "default")
        extraction_source = str(extraction_data.get("extraction_source") or "").strip()
        extraction_version = str(extraction_data.get("extraction_version") or "").strip()
        extracted_text_hash = str(extraction_data.get("extracted_text_hash") or "").strip()
        if not all([artifact_id, extraction_source, extraction_version, extracted_text_hash]):
            raise ValueError("artifact extraction is missing required fields")

        with self._get_connection() as conn:
            existing = conn.execute(
                """
                SELECT id FROM artifact_extractions
                WHERE artifact_id = ? AND extraction_source = ? AND extraction_version = ?
                  AND extracted_text_hash = ?
                LIMIT 1
                """,
                (artifact_id, extraction_source, extraction_version, extracted_text_hash),
            ).fetchone()
            if existing:
                return str(existing["id"])

            extraction_id = str(extraction_data.get("id") or uuid.uuid4())
            conn.execute(
                """
                INSERT INTO artifact_extractions (
                    id, artifact_id, user_id, extraction_source, extraction_version,
                    extracted_text, extracted_text_hash, extraction_timestamp,
                    metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    extraction_id,
                    artifact_id,
                    user_id,
                    extraction_source,
                    extraction_version,
                    extraction_data.get("extracted_text", ""),
                    extracted_text_hash,
                    extraction_data.get("extraction_timestamp", _utcnow_iso()),
                    json.dumps(extraction_data.get("metadata", {})),
                    extraction_data.get("created_at", _utcnow_iso()),
                ),
            )
            return extraction_id

    def get_artifact_extractions(self, artifact_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM artifact_extractions
                WHERE artifact_id = ?
                ORDER BY extraction_timestamp DESC, created_at DESC
                """,
                (artifact_id,),
            ).fetchall()
            return [self._artifact_extraction_row_to_dict(row) for row in rows]

    def replace_artifact_chunks(
        self,
        *,
        artifact_id: str,
        extraction_id: str,
        chunks: List[Dict[str, Any]],
    ) -> int:
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM artifact_chunks WHERE extraction_id = ?",
                (extraction_id,),
            )
            rows = []
            for chunk in chunks:
                rows.append(
                    (
                        chunk.get("id") or str(uuid.uuid4()),
                        artifact_id,
                        extraction_id,
                        chunk.get("chunk_index", 0),
                        chunk.get("content", ""),
                        chunk.get("content_hash", ""),
                        chunk.get("start_offset", 0),
                        chunk.get("end_offset", 0),
                        json.dumps(chunk.get("metadata", {})),
                        chunk.get("created_at", _utcnow_iso()),
                    )
                )
            if rows:
                conn.executemany(
                    """
                    INSERT INTO artifact_chunks (
                        id, artifact_id, extraction_id, chunk_index, content, content_hash,
                        start_offset, end_offset, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            return len(rows)

    def get_artifact_chunks(
        self,
        artifact_id: str,
        *,
        limit: int = 0,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT * FROM artifact_chunks
            WHERE artifact_id = ?
            ORDER BY chunk_index ASC
        """
        params: List[Any] = [artifact_id]
        if limit and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._artifact_chunk_row_to_dict(row) for row in rows]

    def list_artifacts(
        self,
        *,
        user_id: str = "default",
        workspace_id: Optional[str] = None,
        folder_path: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT
                a.*,
                COUNT(DISTINCT b.id) AS binding_count,
                COUNT(DISTINCT e.id) AS extraction_count,
                MAX(e.extraction_timestamp) AS last_extraction_at
            FROM artifact_assets a
            LEFT JOIN artifact_bindings b ON b.artifact_id = a.artifact_id
            LEFT JOIN artifact_extractions e ON e.artifact_id = a.artifact_id
            WHERE a.user_id = ?
        """
        params: List[Any] = [user_id]
        if workspace_id:
            query += """
                AND EXISTS (
                    SELECT 1 FROM artifact_bindings bx
                    WHERE bx.artifact_id = a.artifact_id AND bx.workspace_id = ?
                )
            """
            params.append(workspace_id)
        if folder_path:
            query += """
                AND EXISTS (
                    SELECT 1 FROM artifact_bindings by
                    WHERE by.artifact_id = a.artifact_id AND by.folder_path = ?
                )
            """
            params.append(folder_path)
        query += """
            GROUP BY a.artifact_id
            ORDER BY COALESCE(MAX(e.extraction_timestamp), a.updated_at) DESC
            LIMIT ?
        """
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            out: List[Dict[str, Any]] = []
            for row in rows:
                data = self._artifact_asset_row_to_dict(row)
                data["binding_count"] = row["binding_count"]
                data["extraction_count"] = row["extraction_count"]
                data["last_extraction_at"] = row["last_extraction_at"]
                out.append(data)
            return out

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            asset_row = conn.execute(
                "SELECT * FROM artifact_assets WHERE artifact_id = ? LIMIT 1",
                (artifact_id,),
            ).fetchone()
            if not asset_row:
                return None
            asset = self._artifact_asset_row_to_dict(asset_row)
        asset["bindings"] = self.get_artifact_bindings(artifact_id)
        asset["extractions"] = self.get_artifact_extractions(artifact_id)
        asset["chunks"] = self.get_artifact_chunks(artifact_id)
        return asset

    def export_artifacts(self, *, user_id: str = "default") -> Dict[str, Any]:
        with self._get_connection() as conn:
            assets = conn.execute(
                "SELECT * FROM artifact_assets WHERE user_id = ? ORDER BY created_at ASC",
                (user_id,),
            ).fetchall()
            bindings = conn.execute(
                "SELECT * FROM artifact_bindings WHERE user_id = ? ORDER BY created_at ASC",
                (user_id,),
            ).fetchall()
            extractions = conn.execute(
                "SELECT * FROM artifact_extractions WHERE user_id = ? ORDER BY extraction_timestamp ASC",
                (user_id,),
            ).fetchall()
            chunks = conn.execute(
                """
                SELECT c.*
                FROM artifact_chunks c
                JOIN artifact_assets a ON a.artifact_id = c.artifact_id
                WHERE a.user_id = ?
                ORDER BY c.created_at ASC, c.chunk_index ASC
                """,
                (user_id,),
            ).fetchall()
        return {
            "artifacts_manifest": [self._artifact_asset_row_to_dict(row) for row in assets],
            "artifact_bindings": [self._artifact_binding_row_to_dict(row) for row in bindings],
            "artifact_extractions": [self._artifact_extraction_row_to_dict(row) for row in extractions],
            "artifact_chunks": [self._artifact_chunk_row_to_dict(row) for row in chunks],
        }

    def import_artifacts(
        self,
        payload: Dict[str, Any],
        *,
        user_id: str = "default",
    ) -> Dict[str, int]:
        assets = payload.get("artifacts_manifest") or payload.get("artifacts") or []
        bindings = payload.get("artifact_bindings") or []
        extractions = payload.get("artifact_extractions") or []
        chunks = payload.get("artifact_chunks") or []

        artifact_id_map: Dict[str, str] = {}
        asset_count = 0
        binding_count = 0
        extraction_count = 0
        chunk_count = 0

        for asset in assets:
            if not isinstance(asset, dict):
                continue
            record = dict(asset)
            record["user_id"] = user_id
            artifact_id = self.save_artifact_asset(record)
            artifact_id_map[str(asset.get("artifact_id") or artifact_id)] = artifact_id
            asset_count += 1

        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            record = dict(binding)
            record["artifact_id"] = artifact_id_map.get(
                str(binding.get("artifact_id")),
                str(binding.get("artifact_id") or ""),
            )
            record["user_id"] = user_id
            self.save_artifact_binding(record)
            binding_count += 1

        extraction_id_map: Dict[str, str] = {}
        for extraction in extractions:
            if not isinstance(extraction, dict):
                continue
            record = dict(extraction)
            record["artifact_id"] = artifact_id_map.get(
                str(extraction.get("artifact_id")),
                str(extraction.get("artifact_id") or ""),
            )
            record["user_id"] = user_id
            extraction_id = self.save_artifact_extraction(record)
            extraction_id_map[str(extraction.get("id") or extraction_id)] = extraction_id
            extraction_count += 1

        chunk_groups: Dict[str, List[Dict[str, Any]]] = {}
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            extraction_id = extraction_id_map.get(
                str(chunk.get("extraction_id")),
                str(chunk.get("extraction_id") or ""),
            )
            artifact_id = artifact_id_map.get(
                str(chunk.get("artifact_id")),
                str(chunk.get("artifact_id") or ""),
            )
            record = dict(chunk)
            record["extraction_id"] = extraction_id
            record["artifact_id"] = artifact_id
            chunk_groups.setdefault(extraction_id, []).append(record)

        for extraction_id, group in chunk_groups.items():
            artifact_id = str(group[0].get("artifact_id") or "")
            chunk_count += self.replace_artifact_chunks(
                artifact_id=artifact_id,
                extraction_id=extraction_id,
                chunks=group,
            )

        return {
            "artifacts": asset_count,
            "bindings": binding_count,
            "extractions": extraction_count,
            "chunks": chunk_count,
        }

    def _merge_artifact_state(self, current: str, incoming: str) -> str:
        order = {name: idx for idx, name in enumerate(self._ARTIFACT_STATES)}
        current_idx = order.get(current, 0)
        incoming_idx = order.get(incoming, 0)
        return self._ARTIFACT_STATES[max(current_idx, incoming_idx)]
