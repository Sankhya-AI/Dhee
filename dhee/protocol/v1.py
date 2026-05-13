"""Signed `.dheemem` v1 portable archive format.

The pack is a zip archive containing newline-delimited JSON payloads plus a
signed manifest. Import restores the durable DB rows, artifact substrate,
repo-shared context, and vector index without requiring fresh model calls.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.exceptions import InvalidSignature
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
REPO_CONTEXT_MANIFEST_NAME = "repo_context/manifest.json"
REPO_CONTEXT_ENTRIES_NAME = "repo_context/entries.jsonl"
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
_REPO_CONTEXT_ARCHIVE_NAMES = {REPO_CONTEXT_MANIFEST_NAME, REPO_CONTEXT_ENTRIES_NAME}
_PAYLOAD_ARCHIVE_NAMES = (
    {archive_name for _, archive_name in _FILE_ORDER}
    | {HANDOFF_NAME}
    | _REPO_CONTEXT_ARCHIVE_NAMES
)
_ALLOWED_ARCHIVE_NAMES = _PAYLOAD_ARCHIVE_NAMES | {MANIFEST_NAME}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _path_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


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
    try:
        public_key.verify(
            base64.b64decode(signature_b64.encode("ascii")),
            _canonical_json(manifest_core),
        )
    except InvalidSignature as exc:
        raise ValueError("Manifest signature verification failed") from exc
    return manifest_core


def _safe_archive_name(name: str) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if name.startswith(("/", "\\")) or "\\" in name:
        return False
    if posixpath.normpath(name) != name:
        return False
    parts = name.split("/")
    return all(part and part not in {".", ".."} for part in parts)


def _validate_manifest_core(manifest_core: Dict[str, Any]) -> None:
    if manifest_core.get("format") != "dheemem":
        raise ValueError("Unsupported pack format")
    if str(manifest_core.get("version") or "") != PACK_VERSION:
        raise ValueError(f"Unsupported pack version: {manifest_core.get('version')}")
    files = manifest_core.get("files")
    if not isinstance(files, dict):
        raise ValueError("Manifest files metadata is missing")

    declared = set(files)
    unsafe = sorted(name for name in declared if not _safe_archive_name(str(name)))
    if unsafe:
        raise ValueError(f"Unsafe archive path in manifest: {unsafe[0]}")

    missing = sorted(_PAYLOAD_ARCHIVE_NAMES - declared)
    if missing:
        raise ValueError(f"Pack manifest missing required files: {', '.join(missing)}")
    unexpected = sorted(declared - _PAYLOAD_ARCHIVE_NAMES)
    if unexpected:
        raise ValueError(f"Pack manifest declares unexpected files: {', '.join(unexpected)}")

    for archive_name in sorted(_PAYLOAD_ARCHIVE_NAMES):
        meta = files.get(archive_name)
        if not isinstance(meta, dict):
            raise ValueError(f"Manifest file metadata is invalid for {archive_name}")
        sha = str(meta.get("sha256") or "")
        if not _SHA256_RE.match(sha):
            raise ValueError(f"Manifest sha256 metadata is invalid for {archive_name}")
        records = meta.get("records")
        if records is not None:
            try:
                if int(records) < 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Manifest record count is invalid for {archive_name}") from exc


def _validate_zip_members(zf: ZipFile, manifest_core: Dict[str, Any]) -> None:
    names = zf.namelist()
    if len(names) != len(set(names)):
        raise ValueError("Pack contains duplicate archive members")

    unsafe = sorted(name for name in names if not _safe_archive_name(name))
    if unsafe:
        raise ValueError(f"Unsafe archive path in pack: {unsafe[0]}")

    actual = set(names)
    missing = sorted(_ALLOWED_ARCHIVE_NAMES - actual)
    if missing:
        raise ValueError(f"Pack archive missing required files: {', '.join(missing)}")
    unexpected = sorted(actual - _ALLOWED_ARCHIVE_NAMES)
    if unexpected:
        raise ValueError(f"Pack archive contains unexpected files: {', '.join(unexpected)}")

    declared = set((manifest_core.get("files") or {}).keys()) | {MANIFEST_NAME}
    if actual != declared:
        raise ValueError("Pack archive members do not match signed manifest")


def _read_manifest_and_validate_pack(zf: ZipFile) -> tuple[Dict[str, Any], Dict[str, Any]]:
    names = zf.namelist()
    if len(names) != len(set(names)):
        raise ValueError("Pack contains duplicate archive members")
    if MANIFEST_NAME not in names:
        raise ValueError("Pack manifest is missing")
    unsafe = sorted(name for name in names if not _safe_archive_name(name))
    if unsafe:
        raise ValueError(f"Unsafe archive path in pack: {unsafe[0]}")
    try:
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Pack manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Pack manifest must be a JSON object")
    manifest_core = _verify_manifest(manifest)
    _validate_manifest_core(manifest_core)
    _validate_zip_members(zf, manifest_core)
    return manifest, manifest_core


def _verified_read(zf: ZipFile, manifest_core: Dict[str, Any], archive_name: str) -> bytes:
    meta = (manifest_core.get("files") or {}).get(archive_name) or {}
    raw = zf.read(archive_name)
    actual = _sha256(raw)
    if actual != meta.get("sha256"):
        raise ValueError(f"Hash mismatch for {archive_name}")
    return raw


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


def _default_repo_context_manifest(repo: Path, *, included: bool) -> Dict[str, Any]:
    return {
        "format": "dhee_repo_context",
        "version": PACK_VERSION,
        "included": included,
        "source_repo": str(repo),
        "exported_at": _utcnow(),
        "schema_version": 1,
        "repo_id": "",
        "entry_count": 0,
        "records": 0,
        "source_manifest": {},
    }


def _read_repo_context_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Repo context manifest is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Repo context manifest must be a JSON object: {path}")
    return data


def _read_repo_context_entries(path: Path, context_dir: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError(f"Repo context file is a symlink: {path}")
    if not _path_within(path, context_dir):
        raise ValueError(f"Repo context file escapes context directory: {path}")
    try:
        return _parse_jsonl(path.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Repo context entries are not valid JSONL: {path}") from exc


def _assert_repo_context_safe(entries: List[Dict[str, Any]]) -> None:
    from dhee.hooks.claude_code.privacy import filter_secrets

    for row in entries:
        if not isinstance(row, dict):
            raise ValueError("Repo context entries must be JSON objects")
        text = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        if filter_secrets(text) != text:
            raise ValueError("Repo context contains a likely secret and cannot be packed")


def _repo_context_payload(repo: os.PathLike[str] | str | None) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    repo_root = Path(repo or os.getcwd()).expanduser().resolve()
    context_dir = repo_root / ".dhee" / "context"
    manifest_path = context_dir / "manifest.json"
    entries_path = context_dir / "entries.jsonl"
    if not context_dir.exists():
        return _default_repo_context_manifest(repo_root, included=False), []
    if context_dir.is_symlink():
        raise ValueError(f"Repo context directory is a symlink: {context_dir}")

    context_root = context_dir.resolve(strict=False)
    for path in (manifest_path, entries_path):
        if path.exists() and (path.is_symlink() or not _path_within(path, context_root)):
            raise ValueError(f"Repo context file is unsafe: {path}")

    source_manifest = _read_repo_context_json(manifest_path)
    entries = _read_repo_context_entries(entries_path, context_root)
    _assert_repo_context_safe(entries)

    payload_manifest = _default_repo_context_manifest(repo_root, included=True)
    payload_manifest.update(
        {
            "schema_version": source_manifest.get("schema_version") or 1,
            "repo_id": str(source_manifest.get("repo_id") or ""),
            "entry_count": int(source_manifest.get("entry_count") or len(entries)),
            "records": len(entries),
            "source_manifest": source_manifest,
        }
    )
    return payload_manifest, entries


def export_pack(
    *,
    db: Any,
    vector_store: Any,
    output_path: os.PathLike[str] | str,
    user_id: str = "default",
    key_dir: os.PathLike[str] | str,
    repo: os.PathLike[str] | str | None = None,
) -> Dict[str, Any]:
    rows = _export_rows(db, user_id=user_id)
    repo_for_handoff = str(Path(repo or os.getcwd()).expanduser().resolve())
    handoff = build_handoff_snapshot(db, user_id=user_id, repo=repo_for_handoff)
    repo_context_manifest, repo_context_entries = _repo_context_payload(repo)
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
    repo_context_manifest_raw = json.dumps(
        repo_context_manifest,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    repo_context_entries_raw = _jsonl_bytes(repo_context_entries)
    blobs[REPO_CONTEXT_MANIFEST_NAME] = repo_context_manifest_raw
    blobs[REPO_CONTEXT_ENTRIES_NAME] = repo_context_entries_raw
    file_meta[REPO_CONTEXT_MANIFEST_NAME] = {
        "sha256": _sha256(repo_context_manifest_raw),
        "records": 1,
    }
    file_meta[REPO_CONTEXT_ENTRIES_NAME] = {
        "sha256": _sha256(repo_context_entries_raw),
        "records": len(repo_context_entries),
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
        "counts": {
            **{name: len(rows.get(name, [])) for name, _ in _FILE_ORDER},
            "repo_context_entries": len(repo_context_entries),
        },
        "handoff": handoff,
        "repo_context": repo_context_manifest,
    }


def inspect_pack(path: os.PathLike[str] | str) -> Dict[str, Any]:
    with ZipFile(path, "r") as zf:
        manifest, manifest_core = _read_manifest_and_validate_pack(zf)
        for archive_name in sorted(manifest_core.get("files") or {}):
            _verified_read(zf, manifest_core, archive_name)
        handoff = json.loads(_verified_read(zf, manifest_core, HANDOFF_NAME).decode("utf-8"))
        repo_context = json.loads(
            _verified_read(zf, manifest_core, REPO_CONTEXT_MANIFEST_NAME).decode("utf-8")
        )
    result = dict(manifest)
    result["handoff"] = handoff
    result["repo_context"] = repo_context
    return result


def _handoff_bootstrap(handoff: Dict[str, Any]) -> Dict[str, Any]:
    last_session = handoff.get("last_session")
    if not isinstance(last_session, dict):
        last_session = {}
    return {
        "format": handoff.get("format"),
        "generated_at": handoff.get("generated_at"),
        "continuity_source": handoff.get("continuity_source"),
        "last_session_id": last_session.get("id"),
        "last_session_status": last_session.get("status"),
        "has_thread_state": bool(handoff.get("thread_state")),
        "recent_artifacts": len(handoff.get("recent_artifacts") or []),
        "shared_task_results": len(handoff.get("shared_task_results") or []),
    }


def _repo_context_bootstrap(repo_context: Dict[str, Any], entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "format": repo_context.get("format"),
        "included": bool(repo_context.get("included")),
        "source_repo": repo_context.get("source_repo"),
        "repo_id": repo_context.get("repo_id"),
        "entry_count": int(repo_context.get("entry_count") or 0),
        "records": len(entries),
    }


def _load_pack_payload(
    path: os.PathLike[str] | str,
) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    with ZipFile(path, "r") as zf:
        _manifest, manifest_core = _read_manifest_and_validate_pack(zf)
        rows: Dict[str, List[Dict[str, Any]]] = {}
        for logical_name, archive_name in _FILE_ORDER:
            rows[logical_name] = _parse_jsonl(_verified_read(zf, manifest_core, archive_name))
        handoff = json.loads(_verified_read(zf, manifest_core, HANDOFF_NAME).decode("utf-8"))
        if not isinstance(handoff, dict) or handoff.get("format") != "dhee_handoff":
            raise ValueError("Pack handoff bootstrap is invalid")
        repo_context = json.loads(
            _verified_read(zf, manifest_core, REPO_CONTEXT_MANIFEST_NAME).decode("utf-8")
        )
        if not isinstance(repo_context, dict) or repo_context.get("format") != "dhee_repo_context":
            raise ValueError("Pack repo context manifest is invalid")
        repo_context_entries = _parse_jsonl(_verified_read(zf, manifest_core, REPO_CONTEXT_ENTRIES_NAME))
        _assert_repo_context_safe(repo_context_entries)
    return rows, handoff, repo_context, repo_context_entries


def _load_pack_rows(path: os.PathLike[str] | str) -> Dict[str, List[Dict[str, Any]]]:
    rows, _handoff, _repo_context, _repo_context_entries = _load_pack_payload(path)
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


def _canonical_repo_context_row(row: Dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _merge_repo_context(
    *,
    repo: os.PathLike[str] | str | None,
    repo_context: Dict[str, Any],
    entries: List[Dict[str, Any]],
    strategy: str,
) -> Dict[str, Any]:
    if not entries:
        return {
            "status": "empty",
            "entries": 0,
            "imported": 0,
            "skipped_existing": 0,
            "repo": str(repo) if repo is not None else None,
        }
    if repo is None:
        return {
            "status": "skipped",
            "reason": "repo_not_provided",
            "entries": len(entries),
            "imported": 0,
            "skipped_existing": 0,
            "repo": None,
        }

    from dhee import repo_link

    repo_root = Path(repo).expanduser().resolve()
    preexisting_config = repo_link.repo_config_path(repo_root).exists()
    repo_link._ensure_repo_skeleton(repo_root)
    context_dir = repo_link.repo_context_dir(repo_root)
    entries_path = repo_link.repo_entries_path(repo_root)
    config_path = repo_link.repo_config_path(repo_root)
    if context_dir.is_symlink() or not _path_within(context_dir, repo_root):
        raise ValueError(f"Target repo context directory is unsafe: {context_dir}")
    if entries_path.exists() and entries_path.is_symlink():
        raise ValueError(f"Target repo context entries file is unsafe: {entries_path}")

    source_repo_id = str(repo_context.get("repo_id") or "").strip()
    if source_repo_id:
        cfg = _read_repo_context_json(config_path)
        if not preexisting_config or not cfg.get("repo_id"):
            cfg["repo_id"] = source_repo_id
            cfg["schema_version"] = cfg.get("schema_version") or 1
            cfg["linked_at"] = cfg.get("linked_at") or _utcnow()
            config_path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    _assert_repo_context_safe(entries)
    context_dir.mkdir(parents=True, exist_ok=True)
    if strategy == "replace":
        raw = _jsonl_bytes(entries)
        entries_path.write_bytes(raw)
        imported = len(entries)
        skipped = 0
    else:
        existing_rows = _read_repo_context_entries(entries_path, context_dir.resolve(strict=False))
        existing_signatures = {
            _canonical_repo_context_row(row)
            for row in existing_rows
            if isinstance(row, dict)
        }
        imported_rows: List[Dict[str, Any]] = []
        skipped = 0
        for row in entries:
            signature = _canonical_repo_context_row(row)
            if signature in existing_signatures:
                skipped += 1
                continue
            imported_rows.append(row)
            existing_signatures.add(signature)
        if imported_rows:
            with entries_path.open("ab") as fh:
                raw = _jsonl_bytes(imported_rows)
                if entries_path.stat().st_size and not entries_path.read_bytes().endswith(b"\n"):
                    fh.write(b"\n")
                fh.write(raw)
        imported = len(imported_rows)

    manifest = repo_link.refresh_manifest(repo_root)
    return {
        "status": "imported",
        "repo": str(repo_root),
        "entries": len(entries),
        "imported": imported,
        "skipped_existing": skipped,
        "manifest": manifest,
    }


def import_pack(
    *,
    db: Any,
    vector_store: Any,
    input_path: os.PathLike[str] | str,
    user_id: str = "default",
    strategy: str = "merge",
    repo: os.PathLike[str] | str | None = None,
) -> Dict[str, Any]:
    strategy = str(strategy or "merge").strip().lower()
    if strategy not in {"merge", "replace", "dry-run"}:
        raise ValueError("strategy must be one of: merge, replace, dry-run")

    rows, handoff, repo_context, repo_context_entries = _load_pack_payload(input_path)
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
        "handoff_bootstrap": _handoff_bootstrap(handoff),
        "repo_context": _repo_context_bootstrap(repo_context, repo_context_entries),
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

    repo_context_import = _merge_repo_context(
        repo=repo,
        repo_context=repo_context,
        entries=repo_context_entries,
        strategy=strategy,
    )

    return {
        **preview,
        "cleared": cleared,
        "memory_import": memory_stats,
        "history_imported": history_inserted,
        "distillation_provenance_imported": provenance_inserted,
        "artifact_import": artifact_stats,
        "vectors_imported": imported_vectors,
        "repo_context_import": repo_context_import,
    }
