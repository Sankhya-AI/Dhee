"""gstack adapter — ingest gstack's on-disk memory into Dhee.

gstack (``garrytan/gstack``) is a Claude Code skill pack whose 23 skills
write siloed memory under ``${GSTACK_HOME:-$HOME/.gstack}/projects/<slug>/``.
Retrieval inside gstack is substring-only with no consolidation, no
correction, and no semantic checkpoint recall. Dhee already has all of
those substrates; this adapter just wires gstack's files into the same
``Dhee.remember`` pipeline every other memory flows through.

Public surface:

* :func:`detect` — non-raising discovery. Returns a :class:`DetectedGstack`
  whether gstack is installed or not.
* :func:`backfill` — ingest every learning, timeline event, review, and
  checkpoint section that is not already recorded in the cursor manifest.
* :func:`tail_ingest` — delta-only ingest. Safe to call from session
  hooks on every start/stop; idempotent by construction.

Design notes:

* gstack's own files are never mutated. Reads only.
* ``~/.dhee/gstack_manifest.json`` tracks per-file cursors (byte offset
  for JSONL, mtime for markdown). The manifest is the only idempotency
  mechanism.
* Errors are swallowed at the top level so a malformed JSONL row or a
  missing gstack install never breaks a Dhee session hook.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dhee.adapters import gstack_parser as parser

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "gstack_manifest.json"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _dhee_home() -> Path:
    return Path(os.environ.get("DHEE_DATA_DIR", str(Path.home() / ".dhee")))


def _manifest_path() -> Path:
    return _dhee_home() / _MANIFEST_NAME


def _gstack_home() -> Path:
    return Path(os.environ.get("GSTACK_HOME", str(Path.home() / ".gstack")))


def _gstack_install_marker() -> Path:
    """File whose presence confirms gstack is installed at the default location."""

    return Path.home() / ".claude" / "skills" / "gstack" / "VERSION"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass
class DetectedGstack:
    installed: bool
    install_path: str | None
    version: str | None
    gstack_home: str
    projects: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "install_path": self.install_path,
            "version": self.version,
            "gstack_home": self.gstack_home,
            "projects": list(self.projects),
        }


def detect() -> DetectedGstack:
    """Best-effort discovery. Never raises."""

    marker = _gstack_install_marker()
    installed = marker.exists()
    version: str | None = None
    install_path: str | None = None
    if installed:
        install_path = str(marker.parent)
        try:
            version = marker.read_text(encoding="utf-8").strip() or None
        except OSError:
            version = None

    projects: list[str] = []
    projects_root = _gstack_home() / "projects"
    if projects_root.exists() and projects_root.is_dir():
        for child in sorted(projects_root.iterdir()):
            if child.is_dir():
                projects.append(child.name)

    return DetectedGstack(
        installed=installed,
        install_path=install_path,
        version=version,
        gstack_home=str(_gstack_home()),
        projects=projects,
    )


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _load_manifest() -> dict[str, Any]:
    mp = _manifest_path()
    if not mp.exists():
        return {}
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(data: dict[str, Any]) -> None:
    mp = _manifest_path()
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _project_cursor(manifest: dict[str, Any], slug: str) -> dict[str, Any]:
    projects = manifest.setdefault("projects", {})
    return projects.setdefault(
        slug,
        {
            "learnings_bytes": 0,
            "timeline_bytes": 0,
            "reviews_bytes": {},  # {branch_file_name: bytes_read}
            "checkpoints": {},  # {filename: {"mtime": float, "size": int}}
        },
    )


# ---------------------------------------------------------------------------
# Ingest helpers
# ---------------------------------------------------------------------------


@dataclass
class IngestReport:
    slug: str
    learnings: int = 0
    timeline: int = 0
    reviews: int = 0
    checkpoint_sections: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def atoms(self) -> int:
        return self.learnings + self.timeline + self.reviews + self.checkpoint_sections

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "learnings": self.learnings,
            "timeline": self.timeline,
            "reviews": self.reviews,
            "checkpoint_sections": self.checkpoint_sections,
            "atoms": self.atoms,
            "errors": list(self.errors),
        }


def _read_jsonl_tail(path: Path, start_byte: int) -> tuple[list[str], int]:
    """Read lines from ``path`` starting at ``start_byte``. Return (lines, new_byte_offset).

    Handles truncation (if the file shrank, start over from 0) and partial
    final lines (we only commit whole lines; the trailing partial line is
    left for the next call).
    """

    if not path.exists():
        return [], 0
    size = path.stat().st_size
    if start_byte > size:
        # File was truncated or rotated. Re-read from the top.
        start_byte = 0
    if size == start_byte:
        return [], start_byte

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(start_byte)
        data = fh.read()

    if not data:
        return [], start_byte

    # Only commit full lines. If the file doesn't end with \n, the last
    # chunk is a partial write; hold it back.
    if not data.endswith("\n"):
        last_newline = data.rfind("\n")
        if last_newline == -1:
            return [], start_byte
        committed_len = last_newline + 1
        usable = data[:committed_len]
        new_offset = start_byte + len(usable.encode("utf-8"))
    else:
        usable = data
        new_offset = size

    lines = [ln for ln in usable.splitlines() if ln.strip()]
    return lines, new_offset


def _remember_atoms(dhee: Any, atoms: Iterable[parser.GstackAtom]) -> int:
    stored = 0
    for atom in atoms:
        try:
            dhee.remember(content=atom.content, metadata=atom.metadata)
            stored += 1
        except Exception as exc:  # noqa: BLE001 — best-effort write
            logger.debug("gstack adapter: remember failed for %s: %s", atom.source_key, exc)
    return stored


# ---------------------------------------------------------------------------
# Per-project ingest
# ---------------------------------------------------------------------------


def _ingest_project(
    dhee: Any,
    project_dir: Path,
    slug: str,
    cursor: dict[str, Any],
) -> IngestReport:
    report = IngestReport(slug=slug)

    # --- learnings.jsonl ------------------------------------------------
    learnings_path = project_dir / "learnings.jsonl"
    try:
        lines, new_offset = _read_jsonl_tail(learnings_path, int(cursor.get("learnings_bytes", 0)))
        if lines:
            report.learnings = _remember_atoms(dhee, parser.parse_learnings(lines, slug=slug))
        cursor["learnings_bytes"] = new_offset
    except OSError as exc:
        report.errors.append(f"learnings: {exc}")

    # --- timeline.jsonl -------------------------------------------------
    timeline_path = project_dir / "timeline.jsonl"
    try:
        lines, new_offset = _read_jsonl_tail(timeline_path, int(cursor.get("timeline_bytes", 0)))
        if lines:
            report.timeline = _remember_atoms(dhee, parser.parse_timeline(lines, slug=slug))
        cursor["timeline_bytes"] = new_offset
    except OSError as exc:
        report.errors.append(f"timeline: {exc}")

    # --- <branch>-reviews.jsonl ----------------------------------------
    reviews_cursor = cursor.setdefault("reviews_bytes", {})
    if not isinstance(reviews_cursor, dict):
        reviews_cursor = {}
        cursor["reviews_bytes"] = reviews_cursor
    for review_path in sorted(project_dir.glob("*-reviews.jsonl")):
        branch = review_path.name.removesuffix("-reviews.jsonl")
        try:
            lines, new_offset = _read_jsonl_tail(review_path, int(reviews_cursor.get(review_path.name, 0)))
            if lines:
                report.reviews += _remember_atoms(
                    dhee, parser.parse_reviews(lines, slug=slug, branch=branch)
                )
            reviews_cursor[review_path.name] = new_offset
        except OSError as exc:
            report.errors.append(f"reviews:{review_path.name}: {exc}")

    # --- checkpoints/*.md ----------------------------------------------
    checkpoints_dir = project_dir / "checkpoints"
    cp_cursor = cursor.setdefault("checkpoints", {})
    if not isinstance(cp_cursor, dict):
        cp_cursor = {}
        cursor["checkpoints"] = cp_cursor
    if checkpoints_dir.exists() and checkpoints_dir.is_dir():
        for cp_path in sorted(checkpoints_dir.glob("*.md")):
            try:
                stat = cp_path.stat()
                seen = cp_cursor.get(cp_path.name) or {}
                if isinstance(seen, dict):
                    seen_mtime = float(seen.get("mtime", 0.0) or 0.0)
                    seen_size = int(seen.get("size", 0) or 0)
                else:
                    seen_mtime, seen_size = 0.0, 0
                if stat.st_mtime == seen_mtime and stat.st_size == seen_size:
                    continue
                text = cp_path.read_text(encoding="utf-8", errors="replace")
                _, atoms = parser.parse_checkpoint(cp_path, text, slug=slug)
                report.checkpoint_sections += _remember_atoms(dhee, atoms)
                cp_cursor[cp_path.name] = {"mtime": stat.st_mtime, "size": stat.st_size}
            except OSError as exc:
                report.errors.append(f"checkpoint:{cp_path.name}: {exc}")

    return report


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _iter_project_dirs() -> list[tuple[str, Path]]:
    projects_root = _gstack_home() / "projects"
    if not projects_root.exists() or not projects_root.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for child in sorted(projects_root.iterdir()):
        if child.is_dir():
            out.append((child.name, child))
    return out


def backfill(dhee: Any | None = None, *, reset: bool = False) -> dict[str, Any]:
    """Ingest every delta across every detected gstack project.

    Pass ``reset=True`` to clear the cursor manifest first (re-ingests
    everything; used by ``dhee adapters gstack reingest``).
    """

    if dhee is None:
        dhee = _default_dhee()

    manifest = {} if reset else _load_manifest()
    reports: list[dict[str, Any]] = []
    total = IngestReport(slug="__total__")

    for slug, project_dir in _iter_project_dirs():
        cursor = _project_cursor(manifest, slug)
        report = _ingest_project(dhee, project_dir, slug, cursor)
        reports.append(report.as_dict())
        total.learnings += report.learnings
        total.timeline += report.timeline
        total.reviews += report.reviews
        total.checkpoint_sections += report.checkpoint_sections
        total.errors.extend(report.errors)

    manifest["last_ingest_ts"] = datetime.now(timezone.utc).isoformat()
    manifest.setdefault("schema_version", 1)
    _save_manifest(manifest)

    return {
        "projects": reports,
        "atoms_total": total.atoms,
        "learnings_total": total.learnings,
        "timeline_total": total.timeline,
        "reviews_total": total.reviews,
        "checkpoint_sections_total": total.checkpoint_sections,
        "errors": total.errors,
        "last_ingest_ts": manifest["last_ingest_ts"],
    }


def tail_ingest(dhee: Any | None = None) -> dict[str, Any]:
    """Session-hook-safe delta ingest. Errors are swallowed, never raises."""

    try:
        detected = detect()
        if not detected.installed and not detected.projects:
            return {"atoms_total": 0, "skipped": True, "reason": "gstack_not_detected"}
        return backfill(dhee=dhee, reset=False)
    except Exception as exc:  # noqa: BLE001 — this runs in a hook
        logger.debug("gstack tail_ingest swallowed: %s", exc)
        return {"atoms_total": 0, "error": str(exc)}


def status() -> dict[str, Any]:
    """Report current adapter state without doing any ingest work."""

    detected = detect()
    manifest = _load_manifest()
    return {
        "detected": detected.as_dict(),
        "manifest_path": str(_manifest_path()),
        "last_ingest_ts": manifest.get("last_ingest_ts"),
        "projects_tracked": sorted((manifest.get("projects") or {}).keys()),
    }


def clear_manifest() -> bool:
    """Remove the cursor manifest. Returns True if a file was deleted."""

    mp = _manifest_path()
    if mp.exists():
        mp.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Dhee handle
# ---------------------------------------------------------------------------


def _default_dhee() -> Any:
    """Construct a Dhee handle with the same settings as the session hook."""

    from dhee import Dhee

    return Dhee(
        user_id=os.environ.get("DHEE_USER_ID", "default"),
        auto_context=False,
        auto_checkpoint=False,
    )
