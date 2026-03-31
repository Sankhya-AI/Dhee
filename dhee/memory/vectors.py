"""Vector store operations for memory indexing.

Extracted from memory/main.py — centralizes all vector store interactions:
building index vectors, inserting, deleting, updating, searching.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from dhee.core.echo import EchoResult

logger = logging.getLogger(__name__)


def resolve_memory_id(vector_result: Any) -> str:
    """Extract memory_id from a vector search result."""
    payload = getattr(vector_result, "payload", None) or {}
    return str(payload.get("memory_id") or vector_result.id)


def collapse_vector_results(vector_results: List[Any]) -> List[Any]:
    """Deduplicate vector results by memory_id, keeping highest score."""
    collapsed: Dict[str, Any] = {}
    for result in vector_results:
        memory_id = resolve_memory_id(result)
        existing = collapsed.get(memory_id)
        if not existing or float(result.score) > float(existing.score):
            collapsed[memory_id] = result
    return list(collapsed.values())


def build_index_vectors(
    *,
    memory_id: str,
    content: str,
    primary_text: str,
    embedding: List[float],
    echo_result: Optional[EchoResult],
    metadata: Dict[str, Any],
    categories: List[str],
    user_id: Optional[str],
    agent_id: Optional[str],
    run_id: Optional[str],
    app_id: Optional[str],
    embedder,
    embedding_cache: Optional[Dict[str, List[float]]] = None,
) -> Tuple[List[List[float]], List[Dict[str, Any]], List[str]]:
    """Build all vector index nodes for a memory (primary + echo nodes)."""
    base_payload = dict(metadata)
    base_payload.update(
        {
            "memory_id": memory_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "app_id": app_id,
            "categories": categories,
        }
    )

    vectors: List[List[float]] = []
    payloads: List[Dict[str, Any]] = []
    vector_ids: List[str] = []
    seen: set = set()

    def add_node(
        text: str,
        node_type: str,
        subtype: Optional[str] = None,
        vector: Optional[List[float]] = None,
        node_id: Optional[str] = None,
    ) -> None:
        if not text:
            return
        cleaned = str(text).strip()
        if not cleaned:
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)

        payload = base_payload.copy()
        payload.update(
            {
                "text": cleaned,
                "type": node_type,
            }
        )
        if subtype:
            payload["subtype"] = subtype
        if node_type == "primary":
            payload["memory"] = content
        if echo_result and echo_result.category:
            payload["category"] = echo_result.category

        if vector is not None:
            emb = vector
        elif embedding_cache is not None and cleaned in embedding_cache:
            emb = embedding_cache[cleaned]
        else:
            emb = embedder.embed(cleaned, memory_action="add")
        vectors.append(emb)
        payloads.append(payload)
        vector_ids.append(node_id or str(uuid.uuid4()))

    primary_subtype = "question_form" if primary_text != content else None
    add_node(primary_text, "primary", subtype=primary_subtype, vector=embedding, node_id=memory_id)

    if primary_text != content:
        add_node(content, "echo_node", subtype="content")

    if echo_result:
        for paraphrase in echo_result.paraphrases:
            add_node(paraphrase, "echo_node", subtype="paraphrase")
        for question in echo_result.questions:
            add_node(question, "echo_node", subtype="question")

    return vectors, payloads, vector_ids


class VectorOps:
    """Manages vector store operations for a memory instance.

    Wraps the vector_store and db references. Provides insert, delete,
    update, search, and similarity operations.
    """

    def __init__(self, vector_store, db, embedder):
        self.vector_store = vector_store
        self.db = db
        self.embedder = embedder

    def delete_vectors_for_memory(self, memory_id: str) -> None:
        try:
            vectors = self.vector_store.list(filters={"memory_id": memory_id})
            if not vectors:
                self.vector_store.delete(memory_id)
                return
            for vec in vectors:
                self.vector_store.delete(vec.id)
        except Exception as e:
            logger.error(
                "Failed to delete vectors for memory %s: %s. "
                "Orphaned vector entries may exist.",
                memory_id, e,
            )

    def update_vectors_for_memory(self, memory_id: str, payload_updates: Dict[str, Any]) -> None:
        try:
            vectors = self.vector_store.list(filters={"memory_id": memory_id})
        except Exception as e:
            logger.error("Failed to list vectors for memory %s: %s", memory_id, e)
            return
        if not vectors:
            try:
                existing = self.vector_store.get(memory_id)
                if existing:
                    payload = existing.payload or {}
                    payload.update(payload_updates)
                    self.vector_store.update(memory_id, payload=payload)
            except Exception as e:
                logger.error("Failed to update vector payload for memory %s: %s", memory_id, e)
            return
        for vec in vectors:
            payload = vec.payload or {}
            payload.update(payload_updates)
            try:
                self.vector_store.update(vec.id, payload=payload)
            except Exception as e:
                logger.error("Failed to update vector %s for memory %s: %s", vec.id, memory_id, e)

    def nearest_memory(
        self, embedding: List[float], filters: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], float]:
        results = self.vector_store.search(query=None, vectors=embedding, limit=1, filters=filters)
        if not results:
            return None, 0.0
        memory_id = resolve_memory_id(results[0])
        memory = self.db.get_memory(memory_id)
        if not memory:
            return None, 0.0
        return memory, float(results[0].score)

    def find_similar(
        self, embedding: List[float], filters: Dict[str, Any], threshold: float
    ) -> Optional[Dict[str, Any]]:
        memory, similarity = self.nearest_memory(embedding, filters)
        if memory and similarity >= threshold:
            return memory
        return None
