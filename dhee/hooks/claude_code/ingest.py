"""Document ingestion pipeline for Claude Code hooks.

Ingests markdown reference files (CLAUDE.md, AGENTS.md, SKILL.md, etc.)
into Dhee's vector store as high-strength ``doc_chunk`` memories. These
chunks are then selectively retrieved per-turn by the assembler — only
what matches the current task gets injected, replacing the host agent's
"dump everything" approach.

Lifecycle:
    1. ``ingest_file(dhee, path)`` → chunks → vector store
    2. ``auto_ingest_project(dhee, root)`` → find standard files → ingest each
    3. ``is_stale(path)`` → SHA comparison against manifest → bool

SHA-based diffing: re-ingesting an unchanged file is a no-op. Changed files
get old chunks deleted and new ones written. The manifest lives at
``~/.dhee/doc_manifest.json``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dhee.hooks.claude_code.chunker import Chunk, chunk_markdown, sha256_of

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "doc_manifest.json"
_DOC_STRENGTH = 0.95  # High strength so doc chunks resist decay.

# Standard files to auto-ingest from a project root.
_AUTO_INGEST_GLOBS: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/CLAUDE.md",
    ".claude/settings.local.md",
)


@dataclass
class IngestEntry:
    source_path: str
    source_sha: str
    chunk_ids: list[str] = field(default_factory=list)
    chunk_count: int = 0
    ingested_at: str = ""


@dataclass
class IngestResult:
    source_path: str
    chunks_stored: int = 0
    chunks_deleted: int = 0
    skipped: bool = False
    reason: str = ""


def _manifest_path() -> Path:
    return Path(os.environ.get("DHEE_DATA_DIR", str(Path.home() / ".dhee"))) / _MANIFEST_NAME


def _load_manifest() -> dict[str, dict]:
    mp = _manifest_path()
    if not mp.exists():
        return {}
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_manifest(data: dict) -> None:
    mp = _manifest_path()
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def is_stale(path: str | Path) -> bool:
    """True when the file at ``path`` has changed since last ingest (or was never ingested)."""
    path = Path(path)
    if not path.exists():
        return False
    current_sha = sha256_of(path.read_text(encoding="utf-8"))
    manifest = _load_manifest()
    key = str(path.resolve())
    entry = manifest.get(key)
    if not entry:
        return True
    return entry.get("source_sha") != current_sha


def ingest_file(
    dhee: Any,
    path: str | Path,
    *,
    force: bool = False,
    max_chars: int = 1500,
) -> IngestResult:
    """Chunk a markdown file and store each chunk in Dhee's vector store.

    Returns an ``IngestResult`` describing what happened. On a SHA match
    (file unchanged), returns ``skipped=True`` unless ``force=True``.
    """
    path = Path(path).resolve()
    if not path.exists():
        return IngestResult(str(path), reason="file_not_found")

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return IngestResult(str(path), reason="empty_file")

    current_sha = sha256_of(text)
    manifest = _load_manifest()
    key = str(path)

    if not force:
        existing = manifest.get(key)
        if existing and existing.get("source_sha") == current_sha:
            return IngestResult(str(path), skipped=True, reason="unchanged")

    # Delete old chunks for this file if any.
    deleted = 0
    old_entry = manifest.get(key)
    if old_entry and old_entry.get("chunk_ids"):
        for chunk_id in old_entry["chunk_ids"]:
            try:
                dhee.delete(chunk_id)
                deleted += 1
            except Exception:
                pass

    # Chunk and store.
    chunks = chunk_markdown(text, source_path=str(path), max_chars=max_chars)
    stored_ids: list[str] = []

    for chunk in chunks:
        try:
            result = dhee.remember(
                content=chunk.to_embedded_text(),
                metadata={
                    **chunk.to_metadata(),
                    "strength": _DOC_STRENGTH,
                },
            )
            mid = result.get("id", "")
            if mid:
                stored_ids.append(mid)
        except Exception as exc:
            logger.debug("Failed to store chunk %d of %s: %s", chunk.chunk_index, path, exc)

    # Update manifest.
    manifest[key] = {
        "source_sha": current_sha,
        "chunk_ids": stored_ids,
        "chunk_count": len(stored_ids),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_manifest(manifest)

    return IngestResult(
        source_path=str(path),
        chunks_stored=len(stored_ids),
        chunks_deleted=deleted,
    )


def auto_ingest_project(
    dhee: Any,
    project_root: str | Path | None = None,
) -> list[IngestResult]:
    """Find standard markdown files and ingest any that have changed.

    Scans for CLAUDE.md, AGENTS.md, etc. under ``project_root`` (defaults
    to cwd). Only re-ingests files whose SHA differs from the manifest.
    """
    root = Path(project_root or os.getcwd()).resolve()
    results: list[IngestResult] = []

    for glob_pattern in _AUTO_INGEST_GLOBS:
        candidate = root / glob_pattern
        if candidate.exists() and candidate.is_file():
            r = ingest_file(dhee, candidate)
            results.append(r)

    # Also scan for SKILL.md files.
    skills_dir = root / ".claude" / "skills"
    if skills_dir.exists():
        for skill_md in skills_dir.rglob("SKILL.md"):
            r = ingest_file(dhee, skill_md)
            results.append(r)

    return results


def get_manifest_summary() -> dict[str, Any]:
    """Return a summary of all ingested files."""
    manifest = _load_manifest()
    return {
        "files": len(manifest),
        "total_chunks": sum(e.get("chunk_count", 0) for e in manifest.values()),
        "entries": {
            k: {
                "sha": v.get("source_sha", "")[:12],
                "chunks": v.get("chunk_count", 0),
                "at": v.get("ingested_at", ""),
            }
            for k, v in manifest.items()
        },
    }
