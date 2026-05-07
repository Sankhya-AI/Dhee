"""Per-file content baselines — emit deltas, not duplicates.

The product rule from the founder, paraphrased:

> Only what was the tool call result when first read by Codex or Claude
> Code, save that. From then on, what's updated. People will use the
> first tool read, then on updated ones for context. No wasteful info.

Translation: every file the agent reads has a *baseline* — the content
the agent first saw. Subsequent reads of the same file at the same
content hash add zero new information; emitting them again to the
workspace line just inflates the live block and erodes trust. Reads at
a *changed* hash should emit a small delta ("changed since you last
saw it: +5/-3 lines") rather than the full content all over again.

This module is the durable store + the dedup decision. It does not know
or care about the workspace line itself; ``workspace_line.py`` calls
``check_emit`` to decide whether to publish an emit, and what shape the
emit should take.

Storage: one JSON file per linked repo at
``~/.dhee/file_baselines/<repo_id>.json``. Personal-tier — what *this*
dev's agent has seen on *this* machine. The team-shared "this changed
since last pull" surface is a separate file under ``<repo>/.dhee/``
(future work, not in this module).

Concurrency: every operation is best-effort and tolerates dirty reads.
A torn write costs at most one extra emit (the next read re-establishes
the baseline). Never raises into the caller.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Cap entries per repo so the baseline file stays small on monorepos
# the agent has been crawling for a long time. The cap is large enough
# that real workflows never hit it, and the eviction policy (oldest
# ``last_seen`` first) keeps actively-used files in the cache.
_MAX_PATHS_PER_REPO = 5_000

# Packet kinds whose ``digest`` field genuinely represents the *content*
# of a file the agent just observed. Only these go through the dedup
# gate — emits like ``edit_event`` or ``shared_task_started`` carry
# event metadata, not file content, and must always pass through.
_READ_KINDS: frozenset[str] = frozenset({
    "routed_read",
    "native_read",
    "host_read",
    "artifact_parse",
    "host_parse",
})


def _root() -> Path:
    """Return the baseline-store directory, creating it with 0o700.

    SECURITY: the baseline file leaks the dev's read pattern (which
    paths their agent has touched, when, and at what content hash).
    On a multi-user box this is sensitive: anyone reading
    ``~/.dhee/file_baselines/`` could enumerate the dev's recent work.
    We force 0o700 on the directory at first creation; an existing
    directory is also tightened (best-effort) so upgrades inherit
    the policy without requiring a manual chmod.
    """
    root = Path(os.environ.get("DHEE_DATA_DIR", str(Path.home() / ".dhee"))) / "file_baselines"
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


def _load(repo_id: str) -> Dict[str, Dict[str, Any]]:
    """Load the baseline map for *repo_id*. Empty dict on any error."""
    if not repo_id:
        return {}
    p = _path_for(repo_id)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save(repo_id: str, data: Dict[str, Dict[str, Any]]) -> None:
    """Atomic JSON write with 0o600 from creation. Silent on failure.

    SECURITY: write to the temp file with 0o600 *before* the rename so
    no other local user ever sees a broader-perm version of the file.
    The atomic rename also replaces a symlink target with the regular
    file, defeating an attacker-planted symlink at the destination.
    """
    if not repo_id:
        return
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


def _trim(entries: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if len(entries) <= _MAX_PATHS_PER_REPO:
        return entries
    keep = sorted(
        entries.items(),
        key=lambda kv: float(kv[1].get("last_seen", 0.0) or 0.0),
        reverse=True,
    )[:_MAX_PATHS_PER_REPO]
    return dict(keep)


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _split_lines(text: str) -> list[str]:
    return (text or "").splitlines()


@dataclass
class BaselineDecision:
    """Outcome of checking a tool emit against the per-file baseline.

    * ``action="emit_full"`` — first time we've seen this file (or the
      caller passed empty content / opted out). Caller publishes the
      digest as-is.
    * ``action="suppress"`` — content matches the existing baseline.
      Caller skips emission entirely.
    * ``action="emit_delta"`` — content differs. ``digest`` carries a
      compact delta summary (``+N/-M lines``, optional unified diff
      head) that the caller publishes instead of the raw digest.
    """

    action: str
    digest: str
    metadata: Dict[str, Any]


def check_emit(
    *,
    repo_id: Optional[str],
    source_path: Optional[str],
    content: Optional[str],
    packet_kind: Optional[str],
    digest: str,
    diff_lines: int = 6,
) -> BaselineDecision:
    """Decide whether/how to emit a workspace-line message for this read.

    Inputs:

    * ``repo_id`` — the personal-tier repo identifier. Without it we
      have no scope to dedup against, so we always emit the full digest.
    * ``source_path`` — the absolute path the agent read.
    * ``content`` — the actual file content (or extracted text) the
      agent just observed. ``None`` or empty bypasses the gate.
    * ``packet_kind`` — only kinds in ``_READ_KINDS`` go through dedup;
      anything else (edit events, shared-task lifecycle, etc.) passes
      through with ``emit_full``.
    * ``digest`` — the body the caller intended to publish; carried
      forward verbatim on ``emit_full``.
    * ``diff_lines`` — how many head lines of unified diff to embed in
      a delta emit (default 6, conservative).

    Returns a :class:`BaselineDecision`. Side effect: on
    ``emit_full`` and ``emit_delta`` the baseline is updated to the
    new content, so the *next* read of an unchanged file suppresses.
    """
    fallback = BaselineDecision(action="emit_full", digest=digest, metadata={})

    if not repo_id or not source_path or content is None or not str(content).strip():
        return fallback
    kind = (packet_kind or "").strip().lower()
    if kind not in _READ_KINDS:
        return fallback

    abs_path = os.path.abspath(os.path.expanduser(str(source_path)))
    new_hash = _content_hash(content)
    now = time.time()

    try:
        store = _load(repo_id)
        existing = store.get(abs_path)

        if not existing:
            store[abs_path] = {
                "first_hash": new_hash,
                "last_hash": new_hash,
                "first_seen": now,
                "last_seen": now,
                "first_size": len(content),
                "last_size": len(content),
            }
            store = _trim(store)
            _save(repo_id, store)
            return BaselineDecision(
                action="emit_full",
                digest=digest,
                metadata={"baseline_status": "first_seen", "baseline_hash": new_hash[:12]},
            )

        prev_hash = str(existing.get("last_hash") or "")
        if prev_hash == new_hash:
            existing["last_seen"] = now
            store[abs_path] = existing
            _save(repo_id, store)
            return BaselineDecision(
                action="suppress",
                digest="",
                metadata={"baseline_status": "unchanged", "baseline_hash": new_hash[:12]},
            )

        # Content changed since the last emit — produce a delta digest.
        prev_size = int(existing.get("last_size") or 0)
        first_seen_at = float(existing.get("first_seen") or now)
        delta_summary = _delta_summary(
            old_text="",  # we never persist content, only hashes
            new_text=content,
            old_size=prev_size,
            new_size=len(content),
            head_lines=diff_lines,
            since_ts=first_seen_at,
        )
        existing["last_hash"] = new_hash
        existing["last_seen"] = now
        existing["last_size"] = len(content)
        store[abs_path] = existing
        _save(repo_id, store)
        return BaselineDecision(
            action="emit_delta",
            digest=delta_summary,
            metadata={
                "baseline_status": "changed",
                "baseline_hash": new_hash[:12],
                "previous_hash": prev_hash[:12],
            },
        )
    except Exception:
        return fallback


def update_after_write(
    *,
    repo_id: Optional[str],
    source_path: Optional[str],
    content: Optional[str],
) -> None:
    """Reset the baseline after the agent itself writes/edits a file.

    The agent has just produced new content; that new content is the
    baseline going forward. Without this, the next read of the
    just-written file would emit a "changed since baseline" delta
    against the *pre-edit* content, which is misleading — the agent
    already knows what it wrote.
    """
    if not repo_id or not source_path or content is None:
        return
    abs_path = os.path.abspath(os.path.expanduser(str(source_path)))
    new_hash = _content_hash(content)
    now = time.time()
    try:
        store = _load(repo_id)
        existing = store.get(abs_path) or {
            "first_hash": new_hash,
            "first_seen": now,
            "first_size": len(content),
        }
        existing["last_hash"] = new_hash
        existing["last_seen"] = now
        existing["last_size"] = len(content)
        # Only set first_* once.
        existing.setdefault("first_hash", new_hash)
        existing.setdefault("first_seen", now)
        existing.setdefault("first_size", len(content))
        store[abs_path] = existing
        _save(repo_id, store)
    except Exception:
        return


def forget(repo_id: str, source_path: str) -> None:
    """Drop a baseline entry. Used after a file is deleted from disk."""
    if not repo_id or not source_path:
        return
    abs_path = os.path.abspath(os.path.expanduser(str(source_path)))
    try:
        store = _load(repo_id)
        if store.pop(abs_path, None) is not None:
            _save(repo_id, store)
    except Exception:
        return


def stats(repo_id: str) -> Dict[str, Any]:
    """Return a small summary for ``dhee status`` etc."""
    if not repo_id:
        return {"tracked_files": 0}
    try:
        store = _load(repo_id)
        return {"tracked_files": len(store)}
    except Exception:
        return {"tracked_files": 0}


def _delta_summary(
    *,
    old_text: str,
    new_text: str,
    old_size: int,
    new_size: int,
    head_lines: int,
    since_ts: float,
) -> str:
    """Render a compact 'what changed since baseline' string.

    We don't have the old text persisted (storing every read's body
    would defeat the purpose — wasteful info). The summary is therefore
    coarse: byte/line deltas plus a head excerpt of the *new* content
    so the consumer can orient. If the caller ever decides to persist
    old text, this function will use difflib for a real unified diff.
    """
    old_lines = _split_lines(old_text)
    new_lines = _split_lines(new_text)

    if old_text:
        diff = list(difflib.unified_diff(old_lines, new_lines, n=2))
        if diff:
            head = "\n".join(diff[: max(2, head_lines + 2)])
            return f"changed since baseline\n{head}"

    age = max(0.0, time.time() - since_ts)
    age_label = (
        f"{int(age)}s" if age < 60
        else f"{int(age / 60)}m" if age < 3600
        else f"{int(age / 3600)}h" if age < 86400
        else f"{int(age / 86400)}d"
    )
    line_delta = len(new_lines) - len(old_lines)
    byte_delta = new_size - old_size
    sign = "+" if line_delta >= 0 else ""
    head = "\n".join(line for line in new_lines[: max(1, head_lines)])
    return (
        f"changed since baseline ({age_label} ago) · "
        f"{sign}{line_delta} lines, "
        f"{'+' if byte_delta >= 0 else ''}{byte_delta} bytes\n"
        f"{head}"
    )
