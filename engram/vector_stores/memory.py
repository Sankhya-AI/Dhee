from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List, Optional

from engram.memory.utils import matches_filters
from engram.utils.math import cosine_similarity, cosine_similarity_batch
from engram.vector_stores.base import MemoryResult, VectorStoreBase


class InMemoryVectorStore(VectorStoreBase):
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.collection_name = self.config.get("collection_name", "fadem_memories")
        self.vector_size = self.config.get("embedding_model_dims")
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create_col(self, name: str, vector_size: int, distance: str = "cosine") -> None:
        self.collection_name = name
        self.vector_size = vector_size

    def insert(self, vectors: List[List[float]], payloads: Optional[List[Dict[str, Any]]] = None, ids: Optional[List[str]] = None) -> None:
        payloads = payloads or [{} for _ in vectors]
        if len(payloads) != len(vectors):
            raise ValueError("payloads length must match vectors length")
        if ids is not None and len(ids) != len(vectors):
            raise ValueError("ids length must match vectors length")
        ids = ids or [str(uuid.uuid4()) for _ in vectors]
        with self._lock:
            for vector_id, vector, payload in zip(ids, vectors, payloads):
                self._store[vector_id] = {"vector": vector, "payload": payload}

    def search(self, query: Optional[str], vectors: List[float], limit: int = 5, filters: Optional[Dict[str, Any]] = None) -> List[MemoryResult]:
        with self._lock:
            snapshot = list(self._store.items())

        # Separate filtering from scoring so we can batch-score
        filtered: List[tuple] = []
        for vector_id, record in snapshot:
            payload = record.get("payload", {})
            if filters and not matches_filters(payload, filters):
                continue
            filtered.append((vector_id, record, payload))

        if not filtered:
            return []

        store_vectors = [rec.get("vector", []) for _, rec, _ in filtered]
        scores = cosine_similarity_batch(vectors, store_vectors)

        results: List[MemoryResult] = []
        for (vector_id, _, payload), score in zip(filtered, scores):
            results.append(MemoryResult(id=vector_id, score=score, payload=payload))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

    def delete(self, vector_id: str) -> None:
        with self._lock:
            if vector_id in self._store:
                del self._store[vector_id]

    def update(self, vector_id: str, vector: Optional[List[float]] = None, payload: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            if vector_id not in self._store:
                return
            if vector is not None:
                self._store[vector_id]["vector"] = vector
            if payload is not None:
                self._store[vector_id]["payload"] = payload

    def get(self, vector_id: str) -> Optional[MemoryResult]:
        with self._lock:
            record = self._store.get(vector_id)
            if not record:
                return None
            return MemoryResult(id=vector_id, score=0.0, payload=record.get("payload", {}))

    def list_cols(self) -> List[str]:
        return [self.collection_name]

    def delete_col(self) -> None:
        with self._lock:
            self._store = {}

    def col_info(self) -> Dict[str, Any]:
        return {"name": self.collection_name, "size": len(self._store), "vector_size": self.vector_size}

    def list(self, filters: Optional[Dict[str, Any]] = None, limit: Optional[int] = None) -> List[MemoryResult]:
        results: List[MemoryResult] = []
        with self._lock:
            snapshot = list(self._store.items())
        for vector_id, record in snapshot:
            payload = record.get("payload", {})
            if filters and not matches_filters(payload, filters):
                continue
            results.append(MemoryResult(id=vector_id, score=0.0, payload=payload))
        if limit is not None:
            results = results[:limit]
        return results

    def reset(self) -> None:
        with self._lock:
            self._store = {}
