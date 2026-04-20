"""Read-only provenance / lineage helpers for memories and artifacts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _artifact_summary(db: Any, artifact_id: str) -> Optional[Dict[str, Any]]:
    artifact = db.get_artifact(artifact_id)
    if artifact is None:
        return None
    return {
        "artifact_id": artifact.get("artifact_id"),
        "filename": artifact.get("filename"),
        "mime_type": artifact.get("mime_type"),
        "content_hash": artifact.get("content_hash"),
        "lifecycle_state": artifact.get("lifecycle_state"),
        "attached_at": artifact.get("attached_at"),
        "parsed_at": artifact.get("parsed_at"),
        "indexed_at": artifact.get("indexed_at"),
        "portable_at": artifact.get("portable_at"),
        "bindings": artifact.get("bindings", []),
        "extractions": [
            {
                "id": row.get("id"),
                "extraction_source": row.get("extraction_source"),
                "extraction_version": row.get("extraction_version"),
                "extraction_timestamp": row.get("extraction_timestamp"),
                "extracted_text_hash": row.get("extracted_text_hash"),
                "metadata": row.get("metadata", {}),
            }
            for row in artifact.get("extractions", []) or []
        ],
        "chunk_count": len(artifact.get("chunks", []) or []),
    }


def explain_memory(
    db: Any,
    memory_id: str,
    *,
    history_limit: int = 10,
) -> Dict[str, Any]:
    memory = db.get_memory(memory_id, include_tombstoned=True)
    if memory is None:
        return {"error": "Memory not found", "memory_id": memory_id}

    metadata = dict(memory.get("metadata") or {})
    history = db.get_history(memory_id)[: max(1, history_limit)]
    artifact_id = str(metadata.get("artifact_id") or "").strip()
    artifact = _artifact_summary(db, artifact_id) if artifact_id else None

    sources = []
    derivatives = []
    try:
        sources = db.get_distillation_sources(memory_id)
    except Exception:
        pass
    try:
        derivatives = db.get_distillation_derivatives(memory_id)
    except Exception:
        pass

    warnings: List[str] = []
    if metadata.get("kind") == "artifact_chunk" and artifact is None:
        warnings.append("Artifact chunk metadata points to an artifact that is not present locally.")
    if not history:
        warnings.append("No memory_history rows were found for this memory.")

    return {
        "kind": "memory",
        "memory_id": memory.get("id"),
        "memory": memory.get("memory"),
        "user_id": memory.get("user_id"),
        "layer": memory.get("layer"),
        "strength": memory.get("strength"),
        "created_at": memory.get("created_at"),
        "updated_at": memory.get("updated_at"),
        "content_hash": memory.get("content_hash"),
        "source_type": memory.get("source_type") or metadata.get("source_type"),
        "source_app": memory.get("source_app") or metadata.get("source_app"),
        "source_event_id": memory.get("source_event_id") or metadata.get("source_event_id"),
        "memory_type": memory.get("memory_type"),
        "namespace": memory.get("namespace"),
        "metadata": metadata,
        "history": history,
        "history_count": len(history),
        "distillation": {
            "source_count": len(sources),
            "sources": sources,
            "derivative_count": len(derivatives),
            "derivatives": derivatives,
        },
        "artifact": artifact,
        "warnings": warnings,
    }


def explain_artifact(
    db: Any,
    artifact_id: str,
    *,
    include_extraction_text: bool = False,
    include_chunks: bool = False,
    chunk_limit: int = 5,
    max_text_chars: int = 1200,
) -> Dict[str, Any]:
    artifact = db.get_artifact(artifact_id)
    if artifact is None:
        return {"error": "Artifact not found", "artifact_id": artifact_id}

    extractions = []
    for row in artifact.get("extractions", []) or []:
        item = {
            "id": row.get("id"),
            "extraction_source": row.get("extraction_source"),
            "extraction_version": row.get("extraction_version"),
            "extraction_timestamp": row.get("extraction_timestamp"),
            "extracted_text_hash": row.get("extracted_text_hash"),
            "metadata": row.get("metadata", {}),
        }
        if include_extraction_text:
            item["extracted_text"] = str(row.get("extracted_text", ""))[:max_text_chars]
        extractions.append(item)

    chunks = []
    if include_chunks:
        for row in (artifact.get("chunks", []) or [])[: max(1, chunk_limit)]:
            chunks.append(
                {
                    "id": row.get("id"),
                    "chunk_index": row.get("chunk_index"),
                    "start_offset": row.get("start_offset"),
                    "end_offset": row.get("end_offset"),
                    "content_hash": row.get("content_hash"),
                    "metadata": row.get("metadata", {}),
                    "content": str(row.get("content", ""))[:max_text_chars],
                }
            )

    return {
        "kind": "artifact",
        "artifact_id": artifact.get("artifact_id"),
        "filename": artifact.get("filename"),
        "mime_type": artifact.get("mime_type"),
        "byte_size": artifact.get("byte_size"),
        "content_hash": artifact.get("content_hash"),
        "lifecycle_state": artifact.get("lifecycle_state"),
        "attached_at": artifact.get("attached_at"),
        "parsed_at": artifact.get("parsed_at"),
        "indexed_at": artifact.get("indexed_at"),
        "portable_at": artifact.get("portable_at"),
        "bindings": artifact.get("bindings", []),
        "extractions": extractions,
        "chunk_count": len(artifact.get("chunks", []) or []),
        "chunks": chunks,
    }


def explain_identifier(
    db: Any,
    identifier: str,
    *,
    history_limit: int = 10,
    include_extraction_text: bool = False,
    include_chunks: bool = False,
    chunk_limit: int = 5,
    max_text_chars: int = 1200,
) -> Dict[str, Any]:
    identifier = str(identifier or "").strip()
    if not identifier:
        return {"error": "identifier is required"}
    memory = db.get_memory(identifier, include_tombstoned=True)
    if memory is not None:
        return explain_memory(db, identifier, history_limit=history_limit)
    artifact = db.get_artifact(identifier)
    if artifact is not None:
        return explain_artifact(
            db,
            identifier,
            include_extraction_text=include_extraction_text,
            include_chunks=include_chunks,
            chunk_limit=chunk_limit,
            max_text_chars=max_text_chars,
        )
    return {"error": "Memory or artifact not found", "identifier": identifier}
