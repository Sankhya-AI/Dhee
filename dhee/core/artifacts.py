"""Artifact capture and reuse for host-parsed files.

Dhee never OCRs or LLM-extracts uploaded assets on the hot path. Instead,
it records the host's first successful parse and reuses that extracted body
across later turns, harnesses, and machines.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dhee.core.shared_tasks import publish_shared_task_result
from dhee.hooks.claude_code.assembler import DocMatch
from dhee.hooks.claude_code.chunker import sha256_of
from dhee.router import critical_surface as _critical_surface

SUPPORTED_ARTIFACT_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".csv",
    ".tsv",
    ".json",
    ".txt",
    ".md",
    ".rst",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
}

_PATH_TOKEN_RE = re.compile(r"(?P<path>(?:~|/)[^\s'\"<>]+)")
_FILENAME_TOKEN_RE = re.compile(
    r"\b(?P<name>[A-Za-z0-9._-]+\.(?:pdf|docx?|pptx?|xlsx?|csv|tsv|json|txt|md|png|jpe?g|gif|webp))\b",
    re.IGNORECASE,
)


def is_supported_artifact_path(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in SUPPORTED_ARTIFACT_SUFFIXES


def extract_text_from_host_payload(payload: Any) -> str:
    """Best-effort text recovery from a host/tool response payload."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        parts = [extract_text_from_host_payload(item) for item in payload]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(payload, dict):
        for key in ("content", "text", "result", "output", "body", "stdout", "aggregated_output"):
            value = payload.get(key)
            if value:
                text = extract_text_from_host_payload(value)
                if text:
                    return text
        if payload.get("type") == "text" and payload.get("text"):
            return str(payload.get("text"))
        parts = []
        for value in payload.values():
            text = extract_text_from_host_payload(value)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return str(payload)


def find_prompt_file_references(prompt: str) -> List[str]:
    refs: List[str] = []
    if not prompt:
        return refs
    for match in _PATH_TOKEN_RE.finditer(prompt):
        candidate = match.group("path").rstrip(".,:;)]}")
        if is_supported_artifact_path(candidate):
            refs.append(candidate)
    for match in _FILENAME_TOKEN_RE.finditer(prompt):
        refs.append(match.group("name"))
    seen = set()
    ordered: List[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


def chunk_artifact_text(text: str, *, max_chars: int = 1800) -> List[Dict[str, Any]]:
    text = str(text or "").strip()
    if not text:
        return []
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    chunks: List[Dict[str, Any]] = []
    current: List[str] = []
    current_len = 0
    start_offset = 0
    cursor = 0

    def flush() -> None:
        nonlocal current, current_len, start_offset
        if not current:
            return
        content = "\n\n".join(current).strip()
        end_offset = start_offset + len(content)
        chunks.append(
            {
                "chunk_index": len(chunks),
                "content": content,
                "content_hash": sha256_of(content),
                "start_offset": start_offset,
                "end_offset": end_offset,
                "metadata": {"kind": "artifact_chunk"},
            }
        )
        start_offset = end_offset + 2
        current = []
        current_len = 0

    for block in blocks:
        block_len = len(block)
        if current and current_len + block_len + 2 > max_chars:
            flush()
        if not current:
            start_offset = cursor
        current.append(block)
        current_len += block_len + 2
        cursor += block_len + 2
    flush()
    if not chunks:
        chunks.append(
            {
                "chunk_index": 0,
                "content": text,
                "content_hash": sha256_of(text),
                "start_offset": 0,
                "end_offset": len(text),
                "metadata": {"kind": "artifact_chunk"},
            }
        )
    return chunks


class ArtifactManager:
    """High-level artifact lifecycle manager."""

    def __init__(self, db: Any, *, engram: Any = None):
        self._db = db
        self._engram = engram

    def _record_route_decision(self, decision: Dict[str, Any]) -> None:
        try:
            self._db.record_route_decision(decision)
        except Exception:
            return

    def attach(
        self,
        path: str,
        *,
        user_id: str = "default",
        cwd: Optional[str] = None,
        harness: str = "",
        binding_source: str = "artifact_attached",
        project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        file_info = self._resolve_file_info(path, cwd=cwd)
        if not file_info:
            return None

        asset_id = self._db.save_artifact_asset(
            {
                "user_id": user_id,
                "content_hash": file_info["content_hash"],
                "filename": file_info["filename"],
                "mime_type": file_info["mime_type"],
                "byte_size": file_info["byte_size"],
                "lifecycle_state": "attached",
                "attached_at": file_info["timestamp"],
                "metadata": {
                    "path": file_info["source_path"],
                    "workspace_id": file_info["workspace_id"],
                    "folder_path": file_info["folder_path"],
                    "relative_path": file_info["relative_path"],
                    "external": file_info["external"],
                    **(metadata or {}),
                },
            }
        )
        self._db.save_artifact_binding(
            {
                "artifact_id": asset_id,
                "user_id": user_id,
                "project_id": project_id,
                "workspace_id": file_info["workspace_id"],
                "folder_path": file_info["folder_path"],
                "relative_path": file_info["relative_path"],
                "source_path": file_info["source_path"],
                "harness": harness,
                "binding_source": binding_source,
                "binding_key": self._binding_key(
                    artifact_id=asset_id,
                    source_path=file_info["source_path"],
                    workspace_id=file_info["workspace_id"],
                    harness=harness,
                    binding_source=binding_source,
                ),
                "metadata": {
                    "filename": file_info["filename"],
                    "external": file_info["external"],
                    **(metadata or {}),
                },
            }
        )
        artifact = self._db.get_artifact(asset_id)
        try:
            publish_shared_task_result(
                self._db,
                user_id=user_id,
                packet_kind="artifact_attached",
                tool_name="Artifact",
                digest=f"Attached artifact {file_info['filename']}",
                repo=file_info["workspace_id"] or cwd,
                cwd=cwd,
                source_path=file_info["source_path"],
                source_event_id=self._binding_key(
                    artifact_id=asset_id,
                    source_path=file_info["source_path"],
                    workspace_id=file_info["workspace_id"],
                    harness=harness,
                    binding_source=binding_source,
                ),
                artifact_id=asset_id,
                metadata={
                    "binding_source": binding_source,
                    "filename": file_info["filename"],
                    "workspace_id": file_info["workspace_id"],
                    "folder_path": file_info["folder_path"],
                    "relative_path": file_info["relative_path"],
                    "harness": harness,
                },
                harness=harness or None,
                agent_id=harness or None,
            )
        except Exception:
            pass
        return artifact

    def capture_host_parse(
        self,
        *,
        path: str,
        extracted_text: str,
        user_id: str = "default",
        cwd: Optional[str] = None,
        harness: str = "",
        extraction_source: str,
        extraction_version: str = "host-v1",
        project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        extracted_text = str(extracted_text or "").strip()
        if not extracted_text:
            return None

        attached = self.attach(
            path,
            user_id=user_id,
            cwd=cwd,
            harness=harness,
            binding_source="artifact_parsed",
            project_id=project_id,
            metadata=metadata,
        )
        if not attached:
            return None

        artifact_id = str(attached.get("artifact_id") or "")
        extraction_hash = sha256_of(extracted_text)
        existing_ids = {
            row.get("id")
            for row in self._db.get_artifact_extractions(artifact_id)
            if row.get("extracted_text_hash") == extraction_hash
            and row.get("extraction_source") == extraction_source
            and row.get("extraction_version") == extraction_version
        }

        extraction_id = self._db.save_artifact_extraction(
            {
                "artifact_id": artifact_id,
                "user_id": user_id,
                "extraction_source": extraction_source,
                "extraction_version": extraction_version,
                "extracted_text": extracted_text,
                "extracted_text_hash": extraction_hash,
                "extraction_timestamp": self._timestamp(),
                "metadata": metadata or {},
            }
        )
        created = extraction_id not in existing_ids
        chunks = []
        indexed_count = 0
        if created:
            chunks = chunk_artifact_text(extracted_text)
            for chunk in chunks:
                chunk["metadata"] = {
                    **(chunk.get("metadata") or {}),
                    "source_path": attached["bindings"][0].get("source_path") if attached.get("bindings") else path,
                    "artifact_id": artifact_id,
                    "filename": attached.get("filename"),
                    "extraction_source": extraction_source,
                }
            self._db.replace_artifact_chunks(
                artifact_id=artifact_id,
                extraction_id=extraction_id,
                chunks=chunks,
            )
            indexed_count = self._index_chunks(
                artifact=attached,
                extraction_id=extraction_id,
                chunks=chunks,
                user_id=user_id,
                extraction_source=extraction_source,
            )
            self._db.update_artifact_asset(
                artifact_id,
                {
                    "lifecycle_state": "portable",
                    "parsed_at": self._timestamp(),
                    "indexed_at": self._timestamp(),
                    "portable_at": self._timestamp(),
                },
            )
        else:
            self._db.update_artifact_asset(
                artifact_id,
                {
                    "lifecycle_state": "portable",
                    "parsed_at": self._timestamp(),
                    "portable_at": self._timestamp(),
                },
            )
        primary_binding = (attached.get("bindings") or [{}])[0]
        self._record_route_decision(
            {
                **_critical_surface.artifact_parse_decision(
                    source_path=str(primary_binding.get("source_path") or path),
                    created=created,
                    extracted_text=extracted_text,
                    extraction_source=extraction_source,
                    cwd=cwd,
                    source_event_id=(
                        str((metadata or {}).get("call_id") or "")
                        or f"{artifact_id}:{extraction_id}"
                    ),
                ),
                "user_id": user_id,
                "workspace_id": primary_binding.get("workspace_id"),
                "folder_path": primary_binding.get("folder_path"),
            }
        )
        try:
            publish_shared_task_result(
                self._db,
                user_id=user_id,
                packet_kind="artifact_parsed",
                tool_name="Artifact",
                digest=(
                    f"Parsed {attached.get('filename')} "
                    f"into {len(chunks)} chunks via {extraction_source}"
                ),
                repo=primary_binding.get("workspace_id") or cwd,
                cwd=cwd,
                source_path=str(primary_binding.get("source_path") or path),
                source_event_id=(
                    str((metadata or {}).get("call_id") or "")
                    or f"{artifact_id}:{extraction_source}:{extraction_version}"
                ),
                artifact_id=artifact_id,
                metadata={
                    "extraction_id": extraction_id,
                    "chunk_count": len(chunks),
                    "indexed_count": indexed_count,
                    "created": created,
                    "extraction_source": extraction_source,
                    "extraction_version": extraction_version,
                },
                harness=harness or None,
                agent_id=harness or None,
            )
        except Exception:
            pass
        return {
            "artifact_id": artifact_id,
            "extraction_id": extraction_id,
            "created": created,
            "chunk_count": len(chunks),
            "indexed_count": indexed_count,
        }

    def prompt_matches(
        self,
        prompt: str,
        *,
        user_id: str = "default",
        cwd: Optional[str] = None,
        limit: int = 4,
        attach_missing: bool = True,
    ) -> List[DocMatch]:
        refs = find_prompt_file_references(prompt)
        if not refs:
            return []
        query_terms = self._query_terms(prompt)
        matches: List[DocMatch] = []
        seen_artifacts = set()
        for ref in refs:
            artifact = self._find_artifact_for_reference(ref, user_id=user_id, cwd=cwd)
            if artifact is None and attach_missing and is_supported_artifact_path(ref):
                artifact = self.attach(ref, user_id=user_id, cwd=cwd, binding_source="artifact_attached")
            if not artifact:
                continue
            artifact_id = str(artifact.get("artifact_id") or "")
            if artifact_id in seen_artifacts:
                continue
            seen_artifacts.add(artifact_id)
            source_path = ""
            if artifact.get("bindings"):
                source_path = str(artifact["bindings"][0].get("source_path") or "")
            chunks = self._rank_chunks(
                self._db.get_artifact_chunks(artifact_id),
                query_terms=query_terms,
            )[:limit]
            if chunks:
                latest_extraction = (artifact.get("extractions") or [{}])[0]
                total_chars = len(str(latest_extraction.get("extracted_text") or ""))
                returned_chars = sum(len(str(chunk.get("content", ""))) for chunk in chunks)
                top_score = float(chunks[0].get("_score", 0.0))
                self._record_route_decision(
                    {
                        **_critical_surface.artifact_reuse_decision(
                            source_path=source_path or str(artifact.get("filename", "")),
                            total_extracted_chars=total_chars,
                            returned_chars=returned_chars,
                            top_score=top_score,
                            query_terms_count=len(query_terms),
                            cwd=cwd,
                            source_event_id=f"{artifact_id}:{sha256_of(prompt)[:12]}",
                        ),
                        "user_id": user_id,
                        "workspace_id": (
                            (artifact.get("bindings") or [{}])[0].get("workspace_id")
                        ),
                        "folder_path": (
                            (artifact.get("bindings") or [{}])[0].get("folder_path")
                        ),
                    }
                )
            for chunk in chunks:
                matches.append(
                    DocMatch(
                        text=str(chunk.get("content", "")),
                        source_path=source_path or str(artifact.get("filename", "")),
                        heading_breadcrumb=f"artifact › {artifact.get('filename', '')}",
                        score=float(chunk.get("_score", 1.0)),
                        chunk_index=int(chunk.get("chunk_index", 0)),
                    )
                )
        return matches[:limit]

    def export_payload(self, *, user_id: str = "default") -> Dict[str, Any]:
        return self._db.export_artifacts(user_id=user_id)

    def import_payload(
        self,
        payload: Dict[str, Any],
        *,
        user_id: str = "default",
    ) -> Dict[str, int]:
        stats = self._db.import_artifacts(payload, user_id=user_id)
        if self._engram is None:
            return stats
        for asset in payload.get("artifacts_manifest", []) or payload.get("artifacts", []) or []:
            artifact_id = str(asset.get("artifact_id") or "")
            artifact = self._db.get_artifact(artifact_id)
            if not artifact:
                continue
            extractions = artifact.get("extractions") or []
            if not extractions:
                continue
            latest = extractions[0]
            chunks = self._db.get_artifact_chunks(artifact_id)
            self._index_chunks(
                artifact=artifact,
                extraction_id=str(latest.get("id") or ""),
                chunks=chunks,
                user_id=user_id,
                extraction_source=str(latest.get("extraction_source") or "import"),
            )
        return stats

    def _find_artifact_for_reference(
        self,
        ref: str,
        *,
        user_id: str,
        cwd: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        ref = str(ref or "").strip()
        if not ref:
            return None
        resolved = self._normalize_path(ref, cwd=cwd)
        if is_supported_artifact_path(resolved):
            artifact = self._db.find_artifact_by_source_path(
                resolved,
                user_id=user_id,
                workspace_id=self._workspace_id(cwd),
            )
            if artifact:
                return self._db.get_artifact(str(artifact.get("artifact_id") or ""))
        filename = Path(ref).name
        with self._db._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM artifact_assets
                WHERE user_id = ? AND filename = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_id, filename),
            ).fetchone()
            if row:
                return self._db.get_artifact(str(row["artifact_id"]))
        return None

    def _resolve_file_info(self, path: str, *, cwd: Optional[str]) -> Optional[Dict[str, Any]]:
        resolved = self._normalize_path(path, cwd=cwd)
        if not is_supported_artifact_path(resolved):
            return None
        file_path = Path(resolved)
        if not file_path.exists() or not file_path.is_file():
            return None
        try:
            raw = file_path.read_bytes()
        except OSError:
            return None
        workspace_id = self._workspace_id(cwd)
        relative_path = None
        folder_path = None
        external = True
        if workspace_id:
            try:
                relative_path = str(file_path.relative_to(Path(workspace_id)))
                parent_rel = file_path.parent.relative_to(Path(workspace_id))
                folder_path = "." if str(parent_rel) == "." else str(parent_rel)
                external = False
            except Exception:
                relative_path = file_path.name
                folder_path = str(file_path.parent)
        else:
            relative_path = file_path.name
            folder_path = str(file_path.parent)
        mime_type, _ = mimetypes.guess_type(str(file_path))
        return {
            "source_path": str(file_path),
            "filename": file_path.name,
            "content_hash": hashlib.sha256(raw).hexdigest(),
            "byte_size": len(raw),
            "mime_type": mime_type or "application/octet-stream",
            "workspace_id": workspace_id,
            "folder_path": folder_path,
            "relative_path": relative_path,
            "external": external,
            "timestamp": self._timestamp(),
        }

    def _normalize_path(self, path: str, *, cwd: Optional[str]) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        expanded = os.path.expanduser(raw)
        file_path = Path(expanded)
        if not file_path.is_absolute():
            base = Path(cwd or os.getcwd())
            file_path = base / file_path
        try:
            return str(file_path.resolve())
        except OSError:
            return str(file_path.absolute())

    def _binding_key(
        self,
        *,
        artifact_id: str,
        source_path: str,
        workspace_id: Optional[str],
        harness: str,
        binding_source: str,
    ) -> str:
        raw = "|".join(
            [
                artifact_id,
                source_path or "",
                workspace_id or "",
                harness or "",
                binding_source or "",
            ]
        )
        return sha256_of(raw)

    def _workspace_id(self, cwd: Optional[str]) -> str:
        root = cwd or os.getcwd()
        try:
            return str(Path(root).resolve())
        except OSError:
            return str(Path(root).absolute())

    def _timestamp(self) -> str:
        from dhee.db.sqlite_common import _utcnow_iso

        return _utcnow_iso()

    def _query_terms(self, prompt: str) -> List[str]:
        terms = []
        for word in re.findall(r"[A-Za-z0-9_]{4,}", prompt.lower()):
            if not is_supported_artifact_path(word):
                terms.append(word)
        return terms

    def _rank_chunks(
        self,
        chunks: Iterable[Dict[str, Any]],
        *,
        query_terms: List[str],
    ) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        for chunk in chunks:
            text = str(chunk.get("content", "")).lower()
            score = 1.0
            if query_terms:
                overlap = sum(1 for term in query_terms if term in text)
                score = 0.5 + overlap
            row = dict(chunk)
            row["_score"] = float(score)
            ranked.append(row)
        ranked.sort(key=lambda item: (item.get("_score", 0.0), -int(item.get("chunk_index", 0))), reverse=True)
        return ranked

    def _index_chunks(
        self,
        *,
        artifact: Dict[str, Any],
        extraction_id: str,
        chunks: List[Dict[str, Any]],
        user_id: str,
        extraction_source: str,
    ) -> int:
        if self._engram is None:
            return 0

        bindings = artifact.get("bindings") or []
        primary_binding = bindings[0] if bindings else {}
        count = 0
        filename = str(artifact.get("filename") or "")
        for chunk in chunks:
            content = str(chunk.get("content", "")).strip()
            if not content:
                continue
            metadata = {
                "kind": "artifact_chunk",
                "artifact_id": artifact.get("artifact_id"),
                "extraction_id": extraction_id,
                "source_path": primary_binding.get("source_path"),
                "source_sha": artifact.get("content_hash"),
                "heading_path": [filename],
                "heading_breadcrumb": f"artifact › {filename}",
                "chunk_index": chunk.get("chunk_index", 0),
                "char_count": len(content),
                "workspace_id": primary_binding.get("workspace_id"),
                "folder_path": primary_binding.get("folder_path"),
                "relative_path": primary_binding.get("relative_path"),
                "mime_type": artifact.get("mime_type"),
                "extraction_source": extraction_source,
            }
            self._engram.add(
                content,
                user_id=user_id,
                metadata=metadata,
                categories=["artifact_chunk", filename] if filename else ["artifact_chunk"],
                infer=False,
                source_app=f"artifact:{extraction_source}",
            )
            count += 1
        return count
