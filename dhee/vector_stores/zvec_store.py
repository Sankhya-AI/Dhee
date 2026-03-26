"""zvec vector store implementation.

Uses zvec 0.2.0 (Rust-based) for HNSW vector similarity search with cosine distance.
Directory-based collections at ~/.dhee/zvec/ by default.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from typing import Any, Dict, List, Optional

from dhee.memory.utils import matches_filters
from dhee.vector_stores.base import MemoryResult, VectorStoreBase

logger = logging.getLogger(__name__)

# Promoted scalar fields stored natively in zvec for efficient filtering
_PROMOTED_FIELDS = {"user_id", "agent_id"}

_VECTOR_FIELD_NAME = "embedding"


def _build_filter_string(filters: Dict[str, Any]) -> Optional[str]:
    """Translate a dict of filters into zvec SQL-like filter syntax.

    zvec 0.2.0 uses single = for equality (SQL style).
    """
    parts = []
    for key, value in filters.items():
        if key not in _PROMOTED_FIELDS:
            continue
        if isinstance(value, str):
            parts.append(f"{key} = '{value}'")
        elif isinstance(value, (int, float)):
            parts.append(f"{key} = {value}")
    if not parts:
        return None
    return " AND ".join(parts)


class ZvecStore(VectorStoreBase):
    """Vector store backed by zvec 0.2.0 (Rust HNSW engine)."""

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
        from dhee.configs.base import _dhee_data_dir
        db_path = config.get(
            "path",
            os.path.join(_dhee_data_dir(), "zvec"),
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

        # Try opening existing collection first
        try:
            col = self._zvec.open(col_path)
            return col
        except Exception:
            pass

        # Create new collection with schema
        schema = self._zvec.CollectionSchema(
            name=name,
            fields=[
                self._zvec.FieldSchema("uuid", self._zvec.DataType.STRING),
                self._zvec.FieldSchema("user_id", self._zvec.DataType.STRING),
                self._zvec.FieldSchema("agent_id", self._zvec.DataType.STRING),
                self._zvec.FieldSchema("payload_json", self._zvec.DataType.STRING),
            ],
            vectors=self._zvec.VectorSchema(
                _VECTOR_FIELD_NAME,
                data_type=self._zvec.DataType.VECTOR_FP32,
                dimension=vector_size,
                index_param=self._zvec.HnswIndexParam(metric_type=self._zvec.MetricType.COSINE),
            ),
        )

        try:
            col = self._zvec.create_and_open(col_path, schema)
            return col
        except Exception:
            # Another process may have created it
            return self._zvec.open(col_path)

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
            docs = []
            for vector_id, vector, payload in zip(ids, vectors, payloads):
                user_id = str(payload.get("user_id", ""))
                agent_id = str(payload.get("agent_id", ""))
                payload_json = json.dumps(payload, default=str)

                doc = self._zvec.Doc(
                    id=vector_id,
                    vectors={_VECTOR_FIELD_NAME: vector},
                    fields={
                        "uuid": vector_id,
                        "user_id": user_id,
                        "agent_id": agent_id,
                        "payload_json": payload_json,
                    },
                )
                docs.append(doc)

            try:
                self._collection.upsert(docs)
            except Exception:
                # Fallback: insert one by one
                for doc in docs:
                    try:
                        self._collection.upsert(doc)
                    except Exception as e:
                        logger.warning("zvec upsert failed for %s: %s", doc.id, e)
            # Flush to make data immediately searchable
            try:
                self._collection.flush()
            except Exception:
                pass

    def search(
        self,
        query: Optional[str],
        vectors: List[float],
        limit: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[MemoryResult]:
        self._check_open()

        has_non_promoted = filters and any(
            k not in _PROMOTED_FIELDS for k in filters
        )
        fetch_limit = limit * 3 if has_non_promoted else limit

        zvec_filter = _build_filter_string(filters) if filters else None

        with self._lock:
            try:
                vq = self._zvec.VectorQuery(_VECTOR_FIELD_NAME, vector=vectors)
                kwargs: Dict[str, Any] = {
                    "vectors": vq,
                    "topk": fetch_limit,
                }
                if zvec_filter:
                    kwargs["filter"] = zvec_filter
                raw_results = self._collection.query(**kwargs)
            except Exception as e:
                logger.warning("zvec search failed: %s", e)
                return []

        results = []
        for doc in raw_results:
            payload: Dict[str, Any] = {}
            try:
                pj = doc.field("payload_json") if doc.has_field("payload_json") else ""
                payload = json.loads(pj or "{}")
            except (json.JSONDecodeError, TypeError):
                pass

            if has_non_promoted and not matches_filters(payload, filters):
                continue

            score = float(doc.score) if doc.score is not None else 0.0

            results.append(
                MemoryResult(
                    id=doc.field("uuid") if doc.has_field("uuid") else doc.id,
                    score=score,
                    payload=payload,
                )
            )

        return results[:limit]

    def delete(self, vector_id: str) -> None:
        self._check_open()
        with self._lock:
            try:
                self._collection.delete(vector_id)
                self._collection.flush()
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
                # Fetch existing doc
                fetched = self._collection.fetch(vector_id)
                if not fetched or vector_id not in fetched:
                    return

                existing = fetched[vector_id]
                old_fields = existing.get("fields", {})
                old_payload = {}
                try:
                    old_payload = json.loads(old_fields.get("payload_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass

                if payload is not None:
                    old_payload.update(payload)

                use_vector = vector if vector is not None else (
                    existing.get("vectors", {}).get(_VECTOR_FIELD_NAME, [0.0] * self.vector_size)
                )
                user_id = str(old_payload.get("user_id", old_fields.get("user_id", "")))
                agent_id = str(old_payload.get("agent_id", old_fields.get("agent_id", "")))

                doc = self._zvec.Doc(
                    id=vector_id,
                    vectors={_VECTOR_FIELD_NAME: use_vector},
                    fields={
                        "uuid": vector_id,
                        "user_id": user_id,
                        "agent_id": agent_id,
                        "payload_json": json.dumps(old_payload, default=str),
                    },
                )
                self._collection.upsert(doc)
                self._collection.flush()
            except Exception as e:
                logger.warning("zvec update failed for %s: %s", vector_id, e)

    def get(self, vector_id: str) -> Optional[MemoryResult]:
        self._check_open()
        with self._lock:
            try:
                fetched = self._collection.fetch(vector_id)
                if not fetched or vector_id not in fetched:
                    return None
                existing = fetched[vector_id]
                fields = existing.get("fields", {})
                payload = {}
                try:
                    payload = json.loads(fields.get("payload_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
                return MemoryResult(
                    id=fields.get("uuid", vector_id),
                    score=0.0,
                    payload=payload,
                )
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
            if self._collection is not None:
                try:
                    self._collection.destroy()
                except Exception:
                    pass
            self._collection = None
            if os.path.exists(col_path):
                shutil.rmtree(col_path)

    def col_info(self) -> Dict[str, Any]:
        self._check_open()
        info: Dict[str, Any] = {
            "name": self.collection_name,
            "vector_size": self.vector_size,
            "path": self._collection_path(self.collection_name),
        }
        try:
            stats = self._collection.stats
            info["doc_count"] = stats.doc_count if hasattr(stats, "doc_count") else None
        except Exception:
            pass
        return info

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
                vq = self._zvec.VectorQuery(
                    _VECTOR_FIELD_NAME, vector=[0.0] * self.vector_size
                )
                kwargs: Dict[str, Any] = {
                    "vectors": vq,
                    "topk": effective_limit * 3 if has_non_promoted else effective_limit,
                }
                if zvec_filter:
                    kwargs["filter"] = zvec_filter
                raw_results = self._collection.query(**kwargs)
            except Exception as e:
                logger.warning("zvec list failed: %s", e)
                return []

        results = []
        for doc in raw_results:
            payload: Dict[str, Any] = {}
            try:
                pj = doc.field("payload_json") if doc.has_field("payload_json") else ""
                payload = json.loads(pj or "{}")
            except (json.JSONDecodeError, TypeError):
                pass

            if has_non_promoted and not matches_filters(payload, filters):
                continue

            results.append(
                MemoryResult(
                    id=doc.field("uuid") if doc.has_field("uuid") else doc.id,
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
