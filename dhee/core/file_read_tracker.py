"""Per-repo file-read tracker.

Counts how often each file in a linked repo gets read by an agent. The
signal is *personal* — it lives under ``~/.dhee/`` and never leaves the
machine. Two reasons:

* What files the dev's agent reads is behavioral data. Sharing that with
  teammates would be a privacy regression. Aggregate "hot files for the
  team" is a separate, opt-in problem (Wave 2).
* The counter feeds the local SessionStart hint: "files this dev has
  been touching most this week" → bias for retrieval.

Storage: one JSON file per linked repo at
``~/.dhee/file_reads/<repo_id>.json``. Atomic writes. Cap at the 1000
most-recently-read paths per repo so the file stays small.

The module is best-effort: every operation swallows exceptions and never
raises. Hooks should not fail because the counter couldn't write.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_MAX_PATHS_PER_REPO = 1000


def _root() -> Path:
    """Return the file-reads directory, creating it with 0o700.

    SECURITY: this directory leaks every file path the dev's agent has
    read, with timestamps. On a multi-user box that's recon material;
    enforce owner-only access. Existing directories are also tightened
    so upgrades inherit the policy without manual chmod.
    """
    root = Path(os.environ.get("DHEE_DATA_DIR", str(Path.home() / ".dhee"))) / "file_reads"
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _path_for(repo_id: str) -> Path:
    safe = "".join(c for c in str(repo_id) if c.isalnum() or c in "-_")[:64] or "default"
    return _root() / f"{safe}.json"


def _load(repo_id: str) -> Dict[str, Any]:
    p = _path_for(repo_id)
    if not p.exists():
        return {"reads": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"reads": {}}
        if not isinstance(data.get("reads"), dict):
            data["reads"] = {}
        return data
    except (OSError, json.JSONDecodeError):
        return {"reads": {}}


def _save(repo_id: str, data: Dict[str, Any]) -> None:
    """Atomic JSON write with 0o600 from creation.

    SECURITY: same pattern as file_baseline._save — set 0o600 on the
    temp file before atomic rename, so other local users never see a
    broader-perm transient state and so a planted symlink at the
    destination is replaced rather than written through.
    """
    p = _path_for(repo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{secrets.token_hex(6)}.tmp")
    try:
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _trim(reads: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if len(reads) <= _MAX_PATHS_PER_REPO:
        return reads
    keep = sorted(
        reads.items(),
        key=lambda kv: float(kv[1].get("last_seen", 0.0) or 0.0),
        reverse=True,
    )[:_MAX_PATHS_PER_REPO]
    return dict(keep)


def record_read(*, repo_id: Optional[str], path: str) -> None:
    """Increment the read counter for *path* under *repo_id*. Silent on failure."""
    if not repo_id or not path:
        return
    try:
        path = str(Path(path).resolve())
    except OSError:
        return
    try:
        data = _load(repo_id)
        reads = data.setdefault("reads", {})
        slot = reads.setdefault(path, {"count": 0, "last_seen": 0.0})
        slot["count"] = int(slot.get("count", 0)) + 1
        slot["last_seen"] = time.time()
        data["reads"] = _trim(reads)
        _save(repo_id, data)
    except Exception:
        return


@dataclass
class HotFile:
    path: str
    count: int
    last_seen: float


def top_reads(repo_id: str, *, limit: int = 10) -> List[HotFile]:
    """Return the most-read paths for *repo_id*, hottest first.

    Ranking is ``count`` desc, ``last_seen`` desc as a tiebreaker — paths
    seen many times stay above paths seen once recently.
    """
    if not repo_id:
        return []
    try:
        data = _load(repo_id)
        reads = data.get("reads") or {}
        rows = [
            HotFile(
                path=path,
                count=int(meta.get("count", 0) or 0),
                last_seen=float(meta.get("last_seen", 0.0) or 0.0),
            )
            for path, meta in reads.items()
            if isinstance(meta, dict)
        ]
        rows.sort(key=lambda r: (r.count, r.last_seen), reverse=True)
        return rows[: max(1, int(limit))]
    except Exception:
        return []


def total_reads(repo_id: str) -> int:
    """Sum of all read counts for a repo. 0 on any error."""
    if not repo_id:
        return 0
    try:
        data = _load(repo_id)
        return sum(int((m or {}).get("count", 0) or 0) for m in (data.get("reads") or {}).values())
    except Exception:
        return 0
