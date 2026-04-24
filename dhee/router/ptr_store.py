"""Pointer-keyed raw storage for Dhee router tools.

Each `dhee_*` wrapper stores its full raw output here keyed by a short
pointer (e.g. ``R-1a2b3c4d``). The pointer is returned to the model in
place of the raw content. ``dhee_expand_result(ptr)`` retrieves the raw
on demand.

Storage is filesystem-backed (one plain-text file per pointer). No
embedding, no DB — lookups are O(1) and latency-free.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_DEFAULT_DIR_ENV = "DHEE_ROUTER_PTR_DIR"


def _session_id() -> str:
    """Stable session id scoped to (project, user).

    Historical behaviour keyed on ``pid{os.getpid()}``, which meant every
    MCP restart orphaned prior pointers. This made long-lived ptrs
    degrade silently across restarts — a user who expanded a pointer in
    a new session would land on the reverse-mtime fallback at best, or
    nothing at worst.

    The new key is a short hash of (cwd, user). Two benefits:

    - Pointers written in one MCP process are directly loadable from the
      next — no fallback walk needed.
    - A user running Dhee on two projects keeps their pointer caches
      separate; ripples don't cross.

    ``DHEE_ROUTER_SESSION_ID`` still overrides for tests and for users
    who want to pin behaviour.
    """
    env = os.environ.get("DHEE_ROUTER_SESSION_ID")
    if env:
        return env
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "user"
    key = f"{user}|{cwd}"
    digest = hashlib.sha1(key.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"s-{digest}"


def _root() -> Path:
    custom = os.environ.get(_DEFAULT_DIR_ENV)
    if custom:
        return Path(custom).expanduser()
    try:
        from dhee.configs.base import _dhee_data_dir
        base = Path(_dhee_data_dir())
    except Exception:
        base = Path.home() / ".dhee"
    return base / "router_ptr_cache"


def _session_dir() -> Path:
    d = _root() / _session_id()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_ptr(prefix: str, content: str, extra: str = "") -> str:
    h = hashlib.sha1(f"{extra}|{content}|{time.time_ns()}".encode("utf-8")).hexdigest()
    return f"{prefix}-{h[:10]}"


@dataclass
class StoredPtr:
    ptr: str
    path: Path
    meta_path: Path


def store(content: str, *, tool: str, meta: dict[str, Any] | None = None) -> StoredPtr:
    """Persist `content` under a new pointer and return the StoredPtr."""
    prefix = {
        "Read": "R",
        "Bash": "B",
        "Grep": "G",
        "Agent": "A",
        "Glob": "L",
    }.get(tool, "X")
    ptr = _make_ptr(prefix, content, extra=tool)
    sdir = _session_dir()
    path = sdir / f"{ptr}.txt"
    meta_path = sdir / f"{ptr}.json"
    path.write_text(content, encoding="utf-8")
    if meta:
        import json
        meta_path.write_text(
            json.dumps({**meta, "tool": tool, "ptr": ptr, "stored_at": time.time()}, default=str),
            encoding="utf-8",
        )
    return StoredPtr(ptr=ptr, path=path, meta_path=meta_path)


def load(ptr: str) -> str | None:
    """Retrieve the raw content for a pointer. Returns None if missing."""
    if not ptr or "/" in ptr or ".." in ptr:
        return None
    candidates = [_session_dir() / f"{ptr}.txt"]
    root = _root()
    if root.exists():
        for session in sorted(root.iterdir(), reverse=True):
            if not session.is_dir():
                continue
            candidates.append(session / f"{ptr}.txt")
    for p in candidates:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                continue
    return None


def record_expansion(
    ptr: str,
    *,
    tool: str = "",
    intent: str = "",
    depth: str = "",
    agent_id: str = "",
) -> None:
    """Append an append-only audit record that `ptr` was expanded.

    Per-session JSONL file. ``tool`` / ``intent`` / ``depth`` attribution
    enables Phase 8 bucketed analysis. Best-effort — never raises.
    """
    if not ptr or "/" in ptr or ".." in ptr:
        return
    try:
        import json as _json
        log = _session_dir() / "expansions.jsonl"
        rec = {"ptr": ptr, "at": time.time()}
        if tool:
            rec["tool"] = tool
        if intent:
            rec["intent"] = intent
        if depth:
            rec["depth"] = depth
        if agent_id:
            rec["agent_id"] = agent_id
        with log.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(rec) + "\n")
    except Exception:
        return


def iter_expansion_counts() -> dict[str, int]:
    """Return {session_id: expansion_count} across every session dir."""
    root = _root()
    out: dict[str, int] = {}
    if not root.exists():
        return out
    for sdir in root.iterdir():
        if not sdir.is_dir():
            continue
        log = sdir / "expansions.jsonl"
        if not log.exists():
            continue
        try:
            with log.open("r", encoding="utf-8") as f:
                out[sdir.name] = sum(1 for line in f if line.strip())
        except Exception:
            continue
    return out


def iter_expansion_records() -> list[dict[str, Any]]:
    """Return every parsed expansion record across all session dirs."""
    root = _root()
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for sdir in root.iterdir():
        if not sdir.is_dir():
            continue
        log = sdir / "expansions.jsonl"
        if not log.exists():
            continue
        try:
            import json as _json

            with log.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = _json.loads(line)
                    if isinstance(rec, dict):
                        rec.setdefault("session_id", sdir.name)
                        out.append(rec)
        except Exception:
            continue
    return out


def load_meta(ptr: str) -> dict[str, Any] | None:
    """Retrieve the metadata JSON for a pointer, or None."""
    if not ptr or "/" in ptr or ".." in ptr:
        return None
    import json
    candidates = [_session_dir() / f"{ptr}.json"]
    root = _root()
    if root.exists():
        for session in sorted(root.iterdir(), reverse=True):
            if not session.is_dir():
                continue
            candidates.append(session / f"{ptr}.json")
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None
