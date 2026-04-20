"""Signed `.dheemem` v1 portable archive format.

The pack is a zip archive containing newline-delimited JSON payloads plus a
signed manifest. Import restores the durable DB rows, artifact substrate, and
vector index without requiring fresh model calls.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from dhee.core.artifacts import ArtifactManager
from dhee.core.handoff_snapshot import build_handoff_snapshot

PACK_EXTENSION = ".dheemem"
PACK_VERSION = "1"
MANIFEST_NAME = "manifest.json"
HANDOFF_NAME = "handoff.json"
PRIVATE_KEY_NAME = "protocol_ed25519_private.pem"
PUBLIC_KEY_NAME = "protocol_ed25519_public.pem"

_FILE_ORDER = [
    ("memories", "memories.jsonl"),
    ("memory_history", "memory_history.jsonl"),
    ("distillation_provenance", "distillation_provenance.jsonl"),
    ("vectors", "vector_nodes.jsonl"),
    ("artifacts_manifest", "artifacts_manifest.jsonl"),
    ("artifact_bindings", "artifact_bindings.jsonl"),
    ("artifact_extractions", "artifact_extractions.jsonl"),
    ("artifact_chunks", "artifact_chunks.jsonl"),
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(data: Dict[str, Any]) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _jsonl_bytes(rows: Iterable[Dict[str, Any]]) -> bytes:
    lines = [
        json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        for row in rows
    ]
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")


def _parse_jsonl(raw: bytes) -> List[Dict[str, Any]]:
    text = raw.decode("utf-8").strip()
    if not text:
        return []
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _key_id(public_pem: bytes) -> str:
    return hashlib.sha256(public_pem).hexdigest()[:16]


def _ensure_keypair(key_dir: os.PathLike[str] | str) -> Dict[str, bytes]:
    key_path = Path(key_dir)
    key_path.mkdir(parents=True, exist_ok=True)
    private_path = key_path / PRIVATE_KEY_NAME
    public_path = key_path / PUBLIC_KEY_NAME

    if private_path.exists():
        private_key = serialization.load_pem_private_key(
            private_path.read_bytes(),
            password=None,
        )
    else:
        private_key = Ed25519PrivateKey.generate()
        private_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if not public_path.exists():
        public_path.write_bytes(public_pem)

    private_pem = private_path.read_bytes()
    return {
        "private_pem": private_pem,
        "public_pem": public_pem,
        "key_id": _key_id(public_pem).encode("utf-8"),
    }


def _sign_manifest(manifest_core: Dict[str, Any], *, key_dir: os.PathLike[str] | str) -> Dict[str, Any]:
    keys = _ensure_keypair(key_dir)
    private_key = serialization.load_pem_private_key(keys["private_pem"], password=None)
    payload = _canonical_json(manifest_core)
    signature = private_key.sign(payload)
    return {
        "algorithm": "ed25519",
        "key_id": keys["key_id"].decode("utf-8"),
        "public_key_pem": keys["public_pem"].decode("utf-8"),
        "signature_b64": base64.b64encode(signature).decode("ascii"),
    }


def _verify_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    signature = dict(manifest.get("signature") or {})
    manifest_core = dict(manifest)
    manifest_core.pop("signature", None)
    if signature.get("algorithm") != "ed25519":
        raise ValueError("Unsupported manifest signature algorithm")
    public_pem = str(signature.get("public_key_pem") or "").encode("utf-8")
    signature_b64 = str(signature.get("signature_b64") or "").strip()
    if not public_pem or not signature_b64:
        raise ValueError("Manifest signature is incomplete")
    public_key = serialization.load_pem_public_key(public_pem)
    assert isinstance(public_key, Ed25519PublicKey)
    public_key.verify(
        base64.b64decode(signature_b64.encode("ascii")),
        _canonical_json(manifest_core),
    )
    return manifest_core


def _export_rows(db: Any, *, user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    artifact_payload = ArtifactManager(db).export_payload(user_id=user_id)
    memories = db.get_all_memories(user_id=user_id, limit=100000)
    memory_ids = [str(row.get("id") or "") for row in memories if row.get("id")]

    history: List[Dict[str, Any]] = []
    provenance: List[Dict[str, Any]] = []
    with db._get_connection() as conn:
        if memory_ids:
            placeholders = ",".join("?" for _ in memory_ids)
            history_rows = conn.execute(
                f"""
                SELECT *
                FROM memory_history
                WHERE memory_id IN ({placeholders})
                ORDER BY timestamp ASC, id ASC
                """,
                tuple(memory_ids),
            ).fetchall()
            history = [dict(row) for row in history_rows]

            prov_rows = conn.execute(
                f"""
                SELECT DISTINCT *
                FROM distillation_provenance
                WHERE semantic_memory_id IN ({placeholders})
                   OR episodic_memory_id IN ({placeholders})
                ORDER BY created_at ASC
                """,
                tuple(memory_ids) + tuple(memory_ids),
            ).fetchall()
            provenance = [dict(row) for row in prov_rows]
    return {
        "memories": memories,
        "memory_history": history,
        "distillation_provenance": provenance,
        "vectors": [],
        "artifacts_manifest": artifact_payload.get("artifacts_manifest", []),
        "artifact_bindings": artifact_payload.get("artifact_bindings", []),
        "artifact_extractions": artifact_payload.get("artifact_extractions", []),
        "artifact_chunks": artifact_payload.get("artifact_chunks", []),
    }


def export_pack(
    *,
    db: Any,
    vector_store: Any,
    output_path: os.PathLike[str] | str,
    user_id: str = "default",
    key_dir: os.PathLike[str] | str,
) -> Dict[str, Any]:
    rows = _export_rows(db, user_id=user_id)
    handoff = build_handoff_snapshot(db, user_id=user_id, repo=os.getcwd())
    try:
        rows["vectors"] = vector_store.export_entries(filters={"user_id": user_id}, limit=200000)
    except NotImplementedError:
        rows["vectors"] = []

    blobs: Dict[str, bytes] = {}
    file_meta: Dict[str, Dict[str, Any]] = {}
    for logical_name, archive_name in _FILE_ORDER:
        raw = _jsonl_bytes(rows.get(logical_name, []))
        blobs[archive_name] = raw
        file_meta[archive_name] = {
            "sha256": _sha256(raw),
            "records": len(rows.get(logical_name, [])),
        }
    handoff_raw = json.dumps(handoff, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    blobs[HANDOFF_NAME] = handoff_raw
    file_meta[HANDOFF_NAME] = {
        "sha256": _sha256(handoff_raw),
        "records": 1,
    }

    manifest_core = {
        "format": "dheemem",
        "version": PACK_VERSION,
        "created_at": _utcnow(),
        "user_id": user_id,
        "files": file_meta,
    }
    manifest = dict(manifest_core)
    manifest["signature"] = _sign_manifest(manifest_core, key_dir=key_dir)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True))
        for archive_name, raw in blobs.items():
            zf.writestr(archive_name, raw)

    return {
        "path": str(output),
        "version": PACK_VERSION,
        "user_id": user_id,
        "counts": {name: len(rows.get(name, [])) for name, _ in _FILE_ORDER},
        "handoff": handoff,
    }


def inspect_pack(path: os.PathLike[str] | str) -> Dict[str, Any]:
    with ZipFile(path, "r") as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        manifest_core = _verify_manifest(manifest)
        for archive_name, meta in (manifest_core.get("files") or {}).items():
            raw = zf.read(archive_name)
            actual = _sha256(raw)
            if actual != meta.get("sha256"):
                raise ValueError(f"Hash mismatch for {archive_name}")
        handoff = None
        if HANDOFF_NAME in zf.namelist():
            handoff = json.loads(zf.read(HANDOFF_NAME).decode("utf-8"))
    result = dict(manifest)
    if handoff is not None:
        result["handoff"] = handoff
    return result


def _load_pack_rows(path: os.PathLike[str] | str) -> Dict[str, List[Dict[str, Any]]]:
    with ZipFile(path, "r") as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        manifest_core = _verify_manifest(manifest)
        rows: Dict[str, List[Dict[str, Any]]] = {}
        for logical_name, archive_name in _FILE_ORDER:
            raw = zf.read(archive_name)
            actual = _sha256(raw)
            expected = ((manifest_core.get("files") or {}).get(archive_name) or {}).get("sha256")
            if expected and actual != expected:
                raise ValueError(f"Hash mismatch for {archive_name}")
            rows[logical_name] = _parse_jsonl(raw)
    return rows


def _clear_user_memories(db: Any, vector_store: Any, *, user_id: str) -> Dict[str, int]:
    vector_deleted = 0
    try:
        for result in vector_store.list(filters={"user_id": user_id}, limit=200000):
            vector_store.delete(result.id)
            vector_deleted += 1
    except Exception:
        pass

    memory_deleted = 0
    with db._get_connection() as conn:
        memory_ids = [
            str(row["id"])
            for row in conn.execute("SELECT id FROM memories WHERE user_id = ?", (user_id,)).fetchall()
        ]
        if memory_ids:
            placeholders = ",".join("?" for _ in memory_ids)
            conn.execute(
                f"DELETE FROM memory_history WHERE memory_id IN ({placeholders})",
                tuple(memory_ids),
            )
            try:
                conn.execute(
                    f"""
                    DELETE FROM distillation_provenance
                    WHERE semantic_memory_id IN ({placeholders})
                       OR episodic_memory_id IN ({placeholders})
                    """,
                    tuple(memory_ids) + tuple(memory_ids),
                )
            except Exception:
                pass
            for table, col in (
                ("episodic_events", "memory_id"),
                ("engram_facts", "memory_id"),
                ("engram_context", "memory_id"),
                ("engram_scenes", "memory_id"),
                ("engram_entities", "memory_id"),
                ("engram_links", "source_memory_id"),
            ):
                try:
                    conn.execute(
                        f"DELETE FROM {table} WHERE {col} IN ({placeholders})",
                        tuple(memory_ids),
                    )
                except Exception:
                    pass
            conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
            memory_deleted = len(memory_ids)

        artifact_ids = [
            str(row["artifact_id"])
            for row in conn.execute(
                "SELECT artifact_id FROM artifact_assets WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        ]
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            conn.execute(
                f"DELETE FROM artifact_chunks WHERE artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )
            conn.execute(
                f"DELETE FROM artifact_extractions WHERE artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )
            conn.execute(
                f"DELETE FROM artifact_bindings WHERE artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )
            conn.execute(
                f"DELETE FROM artifact_assets WHERE artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )

    return {"memories": memory_deleted, "vectors": vector_deleted}


def _merge_memories(
    db: Any,
    rows: List[Dict[str, Any]],
    *,
    user_id: str,
) -> Dict[str, int]:
    imported = 0
    skipped_ids = 0
    skipped_hashes = 0
    # Content-hash dedup is meant to catch a NEW import colliding with a
    # memory that pre-existed in the target. If two rows in the SAME pack
    # share a content_hash under distinct IDs, both are legitimately
    # distinct memories (same content, different contexts/timestamps) —
    # collapsing them silently drops a memory. So only skip on content_hash
    # when the existing target row was not imported earlier in this pass.
    imported_hashes: set = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = dict(row)
        record["user_id"] = user_id
        memory_id = str(record.get("id") or "").strip()
        if memory_id and db.get_memory(memory_id, include_tombstoned=True):
            skipped_ids += 1
            continue
        content_hash = str(record.get("content_hash") or "").strip()
        if content_hash and content_hash not in imported_hashes:
            existing = db.get_memory_by_content_hash(content_hash, user_id)
            if existing:
                skipped_hashes += 1
                continue
        db.add_memory(record, log_history=False)
        imported += 1
        if content_hash:
            imported_hashes.add(content_hash)
    return {
        "imported": imported,
        "skipped_existing_ids": skipped_ids,
        "skipped_existing_hashes": skipped_hashes,
    }


def _merge_history(
    db: Any,
    rows: List[Dict[str, Any]],
    *,
    valid_memory_ids: set[str],
) -> int:
    inserted = 0
    with db._get_connection() as conn:
        # Snapshot existing (memory_id, event, old_value, new_value, timestamp)
        # tuples BEFORE we start inserting. Otherwise two history rows from
        # the SAME pack with identical signatures would collide — the first
        # insert would make the second look like a duplicate and drop it.
        # We only want to dedup against rows that pre-existed in the target.
        pre_existing: set = set()
        if valid_memory_ids:
            placeholders = ",".join("?" for _ in valid_memory_ids)
            for r in conn.execute(
                f"""
                SELECT memory_id, event,
                       COALESCE(old_value, ''), COALESCE(new_value, ''),
                       COALESCE(timestamp, '')
                FROM memory_history
                WHERE memory_id IN ({placeholders})
                """,
                tuple(valid_memory_ids),
            ).fetchall():
                pre_existing.add(tuple(r))

        for row in rows:
            if not isinstance(row, dict):
                continue
            memory_id = str(row.get("memory_id") or "").strip()
            if not memory_id or memory_id not in valid_memory_ids:
                continue
            signature = (
                memory_id,
                row.get("event"),
                row.get("old_value") or "",
                row.get("new_value") or "",
                row.get("timestamp") or "",
            )
            if signature in pre_existing:
                continue
            conn.execute(
                """
                INSERT INTO memory_history (
                    memory_id, event, old_value, new_value,
                    old_strength, new_strength, old_layer, new_layer, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    row.get("event"),
                    row.get("old_value"),
                    row.get("new_value"),
                    row.get("old_strength"),
                    row.get("new_strength"),
                    row.get("old_layer"),
                    row.get("new_layer"),
                    row.get("timestamp"),
                ),
            )
            inserted += 1
    return inserted


def _merge_distillation_provenance(
    db: Any,
    rows: List[Dict[str, Any]],
    *,
    valid_memory_ids: set[str],
) -> int:
    inserted = 0
    with db._get_connection() as conn:
        for row in rows:
            if not isinstance(row, dict):
                continue
            semantic_id = str(row.get("semantic_memory_id") or "").strip()
            episodic_id = str(row.get("episodic_memory_id") or "").strip()
            if (
                not semantic_id
                or not episodic_id
                or semantic_id not in valid_memory_ids
                or episodic_id not in valid_memory_ids
            ):
                continue
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO distillation_provenance (
                    id, semantic_memory_id, episodic_memory_id,
                    distillation_run_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row.get("id"),
                    semantic_id,
                    episodic_id,
                    row.get("distillation_run_id"),
                    row.get("created_at"),
                ),
            )
            if conn.total_changes > before:
                inserted += 1
    return inserted


def import_pack(
    *,
    db: Any,
    vector_store: Any,
    input_path: os.PathLike[str] | str,
    user_id: str = "default",
    strategy: str = "merge",
) -> Dict[str, Any]:
    strategy = str(strategy or "merge").strip().lower()
    if strategy not in {"merge", "replace", "dry-run"}:
        raise ValueError("strategy must be one of: merge, replace, dry-run")

    rows = _load_pack_rows(input_path)
    memories = rows.get("memories", [])
    history_rows = rows.get("memory_history", [])
    provenance_rows = rows.get("distillation_provenance", [])
    vectors = rows.get("vectors", [])
    artifact_payload = {
        "artifacts_manifest": rows.get("artifacts_manifest", []),
        "artifact_bindings": rows.get("artifact_bindings", []),
        "artifact_extractions": rows.get("artifact_extractions", []),
        "artifact_chunks": rows.get("artifact_chunks", []),
    }

    existing_ids = 0
    existing_hashes = 0
    for row in memories:
        if not isinstance(row, dict):
            continue
        memory_id = str(row.get("id") or "").strip()
        if memory_id and db.get_memory(memory_id, include_tombstoned=True):
            existing_ids += 1
            continue
        content_hash = str(row.get("content_hash") or "").strip()
        if content_hash and db.get_memory_by_content_hash(content_hash, user_id):
            existing_hashes += 1

    preview = {
        "strategy": strategy,
        "memories": len(memories),
        "vectors": len(vectors),
        "artifacts": len(artifact_payload["artifacts_manifest"]),
        "existing_ids": existing_ids,
        "existing_hashes": existing_hashes,
    }
    if strategy == "dry-run":
        return preview

    cleared = {"memories": 0, "vectors": 0}
    if strategy == "replace":
        cleared = _clear_user_memories(db, vector_store, user_id=user_id)

    memory_stats = _merge_memories(db, memories, user_id=user_id)
    valid_memory_ids = {
        str(row.get("id") or "")
        for row in db.get_all_memories(user_id=user_id, limit=200000)
    }
    history_inserted = _merge_history(db, history_rows, valid_memory_ids=valid_memory_ids)
    provenance_inserted = _merge_distillation_provenance(
        db,
        provenance_rows,
        valid_memory_ids=valid_memory_ids,
    )
    artifact_stats = ArtifactManager(db).import_payload(artifact_payload, user_id=user_id)

    imported_vectors = 0
    if strategy == "replace":
        imported_vectors = vector_store.import_entries(vectors)
    else:
        filtered_vectors: List[Dict[str, Any]] = []
        valid_memory_ids = {
            str(row.get("id") or "")
            for row in db.get_all_memories(user_id=user_id, limit=200000)
        }
        existing_vector_ids = {
            result.id for result in vector_store.list(filters={"user_id": user_id}, limit=200000)
        }
        for entry in vectors:
            vector_id = str(entry.get("id") or "").strip()
            payload = dict(entry.get("payload") or {})
            memory_id = str(payload.get("memory_id") or "").strip()
            if vector_id and vector_id in existing_vector_ids:
                continue
            if memory_id and memory_id not in valid_memory_ids:
                continue
            filtered_vectors.append(entry)
        imported_vectors = vector_store.import_entries(filtered_vectors)

    return {
        **preview,
        "cleared": cleared,
        "memory_import": memory_stats,
        "history_imported": history_inserted,
        "distillation_provenance_imported": provenance_inserted,
        "artifact_import": artifact_stats,
        "vectors_imported": imported_vectors,
    }
