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

# Extended ingest set used by `dhee init` — pulls in the human-authored
# context that already lives in most repos. Order is priority order:
# README first (almost always the repo's elevator pitch), then
# architecture/design docs, then contribution guidance, then everything
# else under docs/. The cap in ``init_ingest_project`` keeps this bounded
# on big monorepos.
_INIT_PRIORITY_FILES: tuple[str, ...] = (
    "README.md",
    "Readme.md",
    "readme.md",
    "ARCHITECTURE.md",
    "DESIGN.md",
    "CONTRIBUTING.md",
    "CONTRIBUTORS.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/CLAUDE.md",
)

# Directory names we never crawl — large, generated, or vendored. Keeps
# the init pass fast and prevents indexing third-party churn the dev
# does not own.
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    ".dhee",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "out",
    ".venv",
    "venv",
    ".tox",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".cache",
    "coverage",
    ".gradle",
    ".idea",
    ".vscode",
})


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


def prune_deleted_files(dhee: Any, project_root: str | Path) -> dict[str, int]:
    """Drop manifest entries (and their chunks) for files that no longer
    exist under *project_root*.

    Re-running ``dhee init`` after a ``git pull`` may surface deletes
    or renames. ``ingest_file`` already deletes old chunks when a
    *changed* file's SHA differs, but it never sees a deleted file
    again — so without explicit pruning the manifest grows with stale
    entries and recall keeps surfacing chunks of deleted docs.

    Scoping: we only prune entries whose ``source_path`` is under
    *project_root*. The shared manifest at ``~/.dhee/doc_manifest.json``
    holds entries for many repos; touching another repo's entries from
    here would be a regression.

    Returns ``{"files_pruned": N, "chunks_deleted": M}``.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        return {"files_pruned": 0, "chunks_deleted": 0}

    manifest = _load_manifest()
    if not manifest:
        return {"files_pruned": 0, "chunks_deleted": 0}

    root_str = str(root) + os.sep
    files_pruned = 0
    chunks_deleted = 0
    keys_to_remove: list[str] = []

    for key, entry in manifest.items():
        # Scope: only entries whose path lives inside this repo.
        if not (key == str(root) or key.startswith(root_str)):
            continue
        if Path(key).exists():
            continue
        # File is gone — drop its chunks and the manifest row.
        for chunk_id in (entry or {}).get("chunk_ids") or []:
            try:
                dhee.delete(chunk_id)
                chunks_deleted += 1
            except Exception:
                pass
        keys_to_remove.append(key)
        files_pruned += 1

    if keys_to_remove:
        for key in keys_to_remove:
            manifest.pop(key, None)
        _save_manifest(manifest)

    return {"files_pruned": files_pruned, "chunks_deleted": chunks_deleted}


def init_ingest_project(
    dhee: Any,
    project_root: str | Path,
    *,
    max_chunks: int = 200,
    force: bool = False,
    prune: bool = True,
) -> tuple[list[IngestResult], dict[str, int]]:
    """Index the markdown surface of a (re-)init'd repo.

    Walks the priority list (README, ARCHITECTURE, CONTRIBUTING, CLAUDE.md,
    AGENTS.md), then any other top-level ``*.md``, then everything under
    ``docs/``. Stops once ``max_chunks`` chunks have been stored across
    the whole pass — big monorepos with hundreds of doc files do not run
    away with the embedding budget.

    Re-runs (after ``git pull``, after editing a doc, after running init
    again because the user feels like it) are cheap and correct:

    * SHA-based skip on unchanged files (``ingest_file`` already does this).
    * Changed files: old chunks deleted, new chunks stored, manifest updated.
    * **Deleted files: chunks pruned** via :func:`prune_deleted_files`.
    * Renamed/moved files: treated as a delete + add — old chunks pruned,
      new chunks stored at the new path.

    Returns ``(results, prune_summary)`` — one ``IngestResult`` per file
    considered, plus a small dict describing what was pruned.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        return [], {"files_pruned": 0, "chunks_deleted": 0}

    # Prune first so the chunk-cap budget below isn't blocked by stale
    # entries that would be deleted anyway.
    prune_summary = (
        prune_deleted_files(dhee, root) if prune else {"files_pruned": 0, "chunks_deleted": 0}
    )

    seen: set[Path] = set()
    ordered: list[Path] = []

    # 1. Priority list — exact filenames at the repo root.
    for name in _INIT_PRIORITY_FILES:
        candidate = root / name
        if candidate.is_file():
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                ordered.append(resolved)

    # 2. Other top-level ``*.md`` so ad-hoc repo notes get indexed too.
    for entry in sorted(root.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() != ".md":
            continue
        resolved = entry.resolve()
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)

    # 3. ``docs/`` (and its subdirs) — sorted for stable ingest order.
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in _walk_md(docs_dir):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                ordered.append(resolved)

    results: list[IngestResult] = []
    chunks_used = 0
    for path in ordered:
        if chunks_used >= max_chunks:
            results.append(IngestResult(str(path), skipped=True, reason="chunk_cap_reached"))
            continue
        result = ingest_file(dhee, path, force=force)
        results.append(result)
        if not result.skipped:
            chunks_used += result.chunks_stored

    return results, prune_summary


def _walk_md(root: Path) -> list[Path]:
    """Yield ``*.md`` files under *root*, skipping vendored/generated dirs."""
    out: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # Mutate dirnames in place so os.walk skips these subtrees.
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
            for name in sorted(filenames):
                if name.lower().endswith(".md"):
                    out.append(Path(dirpath) / name)
    except OSError:
        return []
    return out


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
