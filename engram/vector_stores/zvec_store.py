"""zvec vector store implementation.

Uses zvec (Rust-based) for HNSW vector similarity search with cosine distance.
Directory-based collections at ~/.engram/zvec/ by default.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from typing import Any, Dict, List, Optional

from engram.memory.utils import matches_filters
from engram.vector_stores.base import MemoryResult, VectorStoreBase

logger = logging.getLogger(__name__)

# Promoted scalar fields stored natively in zvec for efficient filtering
_PROMOTED_FIELDS = {"user_id", "agent_id"}


def _build_filter_string(filters: Dict[str, Any]) -> Optional[str]:
    """Translate a dict of filters into zvec SQL-like filter syntax.

    zvec supports: field == 'value', field != 'value', AND/OR grouping.
    We only translate promoted scalar fields; remaining filters are applied
    post-search via matches_filters().
    """
    parts = []
    for key, value in filters.items():
        if key not in _PROMOTED_FIELDS:
            continue
        if isinstance(value, str):
            parts.append(f"{key} == '{value}'")
        elif isinstance(value, (int, float)):
            parts.append(f"{key} == {value}")
    if not parts:
        return None
    return " AND ".join(parts)


class ZvecStore(VectorStoreBase):
    """Vector store backed by zvec (Rust HNSW engine)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self.config = config
        self.collection_name = config.get("collection_name", "fadem_memories")
        self.vector_size = (
            config.get("embedding_model_dims")
            or config.get("vector_size")
            or config.get("embedding_dims")
            or 1536
        )
        db_path = config.get(
            "path",
            os.path.join(os.path.expanduser("~"), ".engram", "zvec"),
        )
        os.makedirs(db_path, exist_ok=True)
        self._db_path = db_path

        self._lock = threading.RLock()
        self._closed = False

        import zvec
        self._zvec = zvec

        self._collection = self._ensure_collection(self.collection_name, self.vector_size)

    def _collection_path(self, name: str) -> str:
        return os.path.join(self._db_path, name)

    def _ensure_collection(self, name: str, vector_size: int):
        """Open or create a zvec collection with HNSW index."""
        col_path = self._collection_path(name)

        schema = {
            "dims": vector_size,
            "metric": "cosine",
            "fields": {
                "user_id": "string",
                "agent_id": "string",
                "payload_json": "string",
                "uuid": "string",
            },
        }

        try:
            col = self._zvec.Collection.open(col_path)
            return col
        except Exception:
            pass

        try:
            col = self._zvec.Collection.create(col_path, schema)
            return col
        except Exception:
            # Collection may have been created by another process
            col = self._zvec.Collection.open(col_path)
            return col

    def create_col(self, name: str, vector_size: int, distance: str = "cosine") -> None:
        self._check_open()
        self._ensure_collection(name, vector_size)

    def insert(
        self,
        vectors: List[List[float]],
        payloads: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ) -> None:
        self._check_open()
        payloads = payloads or [{} for _ in vectors]
        if len(payloads) != len(vectors):
            raise ValueError("payloads length must match vectors length")
        if ids is not None and len(ids) != len(vectors):
            raise ValueError("ids length must match vectors length")
        ids = ids or [str(uuid.uuid4()) for _ in vectors]

        for vector in vectors:
            if len(vector) != self.vector_size:
                raise ValueError(
                    f"Vector has {len(vector)} dimensions, expected {self.vector_size}"
                )

        with self._lock:
            for vector_id, vector, payload in zip(ids, vectors, payloads):
                # Extract promoted fields from payload
                user_id = str(payload.get("user_id", ""))
                agent_id = str(payload.get("agent_id", ""))

                # Remaining payload as JSON
                payload_json = json.dumps(payload, default=str)

                # Check if UUID already exists (upsert semantics)
                try:
                    existing = self._collection.search(
                        vector=vector,
                        limit=1,
                        filter=f"uuid == '{vector_id}'",
                    )
                    if existing and len(existing) > 0:
                        # Delete existing entry, then re-insert
                        for entry in existing:
                            try:
                                self._collection.delete(entry["id"])
                            except Exception:
                                pass
                except Exception:
                    pass

                self._collection.insert(
                    vector=vector,
                    fields={
                        "uuid": vector_id,
                        "user_id": user_id,
                        "agent_id": agent_id,
                        "payload_json": payload_json,
                    },
                )

    def search(
        self,
        query: Optional[str],
        vectors: List[float],
        limit: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[MemoryResult]:
        self._check_open()

        # Over-fetch when post-filtering is needed
        has_non_promoted = filters and any(
            k not in _PROMOTED_FIELDS for k in filters
        )
        fetch_limit = limit * 3 if has_non_promoted else limit

        zvec_filter = _build_filter_string(filters) if filters else None

        with self._lock:
            try:
                kwargs: Dict[str, Any] = {
                    "vector": vectors,
                    "limit": fetch_limit,
                }
                if zvec_filter:
                    kwargs["filter"] = zvec_filter
                raw_results = self._collection.search(**kwargs)
            except Exception as e:
                logger.warning("zvec search failed: %s", e)
                return []

        results = []
        for item in raw_results:
            fields = item.get("fields", {})
            payload: Dict[str, Any] = {}
            try:
                payload = json.loads(fields.get("payload_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

            # Post-filter on non-promoted fields
            if has_non_promoted and not matches_filters(payload, filters):
                continue

            score = float(item.get("score", 0.0))

            results.append(
                MemoryResult(
                    id=fields.get("uuid", ""),
                    score=score,
                    payload=payload,
                )
            )

        return results[:limit]

    def delete(self, vector_id: str) -> None:
        self._check_open()
        with self._lock:
            try:
                # Find by uuid field
                # Use a dummy vector search with filter to find the internal id
                results = self._collection.search(
                    vector=[0.0] * self.vector_size,
                    limit=1,
                    filter=f"uuid == '{vector_id}'",
                )
                for entry in results:
                    self._collection.delete(entry["id"])
            except Exception as e:
                logger.warning("zvec delete failed for %s: %s", vector_id, e)

    def update(
        self,
        vector_id: str,
        vector: Optional[List[float]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._check_open()
        with self._lock:
            try:
                # Find existing entry
                results = self._collection.search(
                    vector=[0.0] * self.vector_size,
                    limit=1,
                    filter=f"uuid == '{vector_id}'",
                )
                if not results:
                    return

                entry = results[0]
                old_fields = entry.get("fields", {})
                old_payload = {}
                try:
                    old_payload = json.loads(old_fields.get("payload_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass

                # Merge payload
                if payload is not None:
                    old_payload.update(payload)

                # Delete old entry
                self._collection.delete(entry["id"])

                # Re-insert with updated data
                use_vector = vector if vector is not None else entry.get("vector", [0.0] * self.vector_size)
                user_id = str(old_payload.get("user_id", old_fields.get("user_id", "")))
                agent_id = str(old_payload.get("agent_id", old_fields.get("agent_id", "")))

                self._collection.insert(
                    vector=use_vector,
                    fields={
                        "uuid": vector_id,
                        "user_id": user_id,
                        "agent_id": agent_id,
                        "payload_json": json.dumps(old_payload, default=str),
                    },
                )
            except Exception as e:
                logger.warning("zvec update failed for %s: %s", vector_id, e)

    def get(self, vector_id: str) -> Optional[MemoryResult]:
        self._check_open()
        with self._lock:
            try:
                results = self._collection.search(
                    vector=[0.0] * self.vector_size,
                    limit=1,
                    filter=f"uuid == '{vector_id}'",
                )
                if not results:
                    return None
                fields = results[0].get("fields", {})
                payload = {}
                try:
                    payload = json.loads(fields.get("payload_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
                return MemoryResult(id=fields.get("uuid", vector_id), score=0.0, payload=payload)
            except Exception:
                return None

    def list_cols(self) -> List[str]:
        self._check_open()
        cols = []
        if os.path.isdir(self._db_path):
            for entry in os.listdir(self._db_path):
                full_path = os.path.join(self._db_path, entry)
                if os.path.isdir(full_path):
                    cols.append(entry)
        return cols

    def delete_col(self) -> None:
        self._check_open()
        import shutil
        col_path = self._collection_path(self.collection_name)
        with self._lock:
            self._collection = None
            if os.path.exists(col_path):
                shutil.rmtree(col_path)

    def col_info(self) -> Dict[str, Any]:
        self._check_open()
        return {
            "name": self.collection_name,
            "vector_size": self.vector_size,
            "path": self._collection_path(self.collection_name),
        }

    def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryResult]:
        self._check_open()
        effective_limit = limit or 100

        zvec_filter = _build_filter_string(filters) if filters else None
        has_non_promoted = filters and any(
            k not in _PROMOTED_FIELDS for k in filters
        )

        with self._lock:
            try:
                kwargs: Dict[str, Any] = {
                    "vector": [0.0] * self.vector_size,
                    "limit": effective_limit * 3 if has_non_promoted else effective_limit,
                }
                if zvec_filter:
                    kwargs["filter"] = zvec_filter
                raw_results = self._collection.search(**kwargs)
            except Exception as e:
                logger.warning("zvec list failed: %s", e)
                return []

        results = []
        for item in raw_results:
            fields = item.get("fields", {})
            payload: Dict[str, Any] = {}
            try:
                payload = json.loads(fields.get("payload_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

            if has_non_promoted and not matches_filters(payload, filters):
                continue

            results.append(
                MemoryResult(
                    id=fields.get("uuid", ""),
                    score=0.0,
                    payload=payload,
                )
            )

        return results[:effective_limit]

    def reset(self) -> None:
        self._check_open()
        self.delete_col()
        self._collection = self._ensure_collection(self.collection_name, self.vector_size)

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("ZvecStore is closed")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._collection = None
