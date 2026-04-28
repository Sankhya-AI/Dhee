"""Personal vs Repo context — link a git repo into Dhee.

Two stores:

* **Personal** lives at ``~/.dhee/`` (SQLite + agent state). It never
  leaves the machine. Every engram, raw tool digest, web fetch and
  research scrap lands here first.
* **Repo** lives at ``<repo>/.dhee/context/``. It is a small set of
  JSON/JSONL files that ride along with the repo through normal
  ``git push``/``git pull``. Anything in here is shared with every
  developer who clones the repo and has Dhee installed.

The flow:

1. ``curl … | sh`` installs Dhee (existing harness installer).
2. ``dhee link [path]`` registers a git repo with this machine. We
   create ``<repo>/.dhee/`` (if missing), drop refresh hooks for
   pull/checkout/rebase plus a pre-push conflict check, and mirror the repo
   into the UI workspace store so the canvas shows it. Multiple repos can
   be linked.
3. While working in any session whose ``cwd`` is under a linked repo,
   memory queries fuse personal results with repo-context entries.
4. The user (or, eventually, an end-of-session distiller) calls
   ``dhee promote <memory_id>`` to copy a personal memory into the
   repo's shared context. ``dhee demote <entry_id>`` does the
   reverse.
5. When teammates ``git pull``, the post-merge hook calls
   ``dhee context refresh`` and the new entries are immediately
   visible to *their* coding agent.

The on-disk layout under ``<repo>/.dhee/``::

    config.json                   {repo_id, schema_version, linked_at}
    context/
        manifest.json             {schema_version, repo_id, entry_count, updated_at}
        entries.jsonl             one JSON object per line (append-only, with tombstones)
    .gitattributes                entries.jsonl merge=union  ← painless merges

Every entry has a stable id, a kind, the body, provenance, a parent hash for
optimistic concurrency, and a ``deleted`` tombstone flag (since the file is
git-tracked, real deletion would create merge churn — tombstones are
git-friendly). If two developers edit the same entry concurrently, Git's
union merge keeps both heads and Dhee surfaces a conflict instead of silently
choosing a winner.

Public API is deliberately small. Side-effects (mirroring into the
workspace store, installing git hooks) are explicit and idempotent.
"""

from __future__ import annotations

import json
import os
import secrets
import hashlib
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1


class ContextConflictError(RuntimeError):
    """Raised when repo-shared context has divergent Git-merged heads."""

    def __init__(self, entry_id: str, message: str, *, conflicts: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.entry_id = entry_id
        self.conflicts = conflicts or []

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _personal_root() -> Path:
    """Directory for machine-local Dhee state."""
    return Path.home() / ".dhee"


def _links_path() -> Path:
    """Personal registry of repos this machine has linked."""
    return _personal_root() / "links.json"


def _workspace_store_path() -> Path:
    """Existing UI workspace store. Linking mirrors into it so the
    canvas / sessions view picks up the repo automatically."""
    return _personal_root() / "local_context_folders.json"


def repo_dhee_dir(repo_root: Path) -> Path:
    return repo_root / ".dhee"


def repo_context_dir(repo_root: Path) -> Path:
    return repo_dhee_dir(repo_root) / "context"


def repo_config_path(repo_root: Path) -> Path:
    return repo_dhee_dir(repo_root) / "config.json"


def repo_manifest_path(repo_root: Path) -> Path:
    return repo_context_dir(repo_root) / "manifest.json"


def repo_entries_path(repo_root: Path) -> Path:
    return repo_context_dir(repo_root) / "entries.jsonl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """ULID-flavoured id: time-prefixed so entries.jsonl sorts naturally,
    plus 12 chars of randomness so concurrent writers on different
    machines never collide."""
    ms = int(time.time() * 1000)
    return f"{ms:012x}-{secrets.token_hex(6)}"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _resolve(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.expanduser(str(path))).resolve()


def _git_top(path: Path) -> Optional[Path]:
    """Return the git toplevel for *path*, or ``None`` if not in a repo."""
    probe = path if path.is_dir() else path.parent
    try:
        out = subprocess.check_output(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return None
    return Path(out).resolve() if out else None


def _path_within(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([str(child), str(parent)]) == str(parent)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Entry model
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    id: str
    kind: str
    title: str
    content: str
    created_at: str
    created_by: str
    meta: Dict[str, Any] = field(default_factory=dict)
    source_memory_id: Optional[str] = None
    parent_hash: Optional[str] = None
    content_hash: Optional[str] = None
    updated_at: Optional[str] = None
    deleted: bool = False

    def to_json(self) -> Dict[str, Any]:
        content_hash = self.content_hash or _entry_hash(
            kind=self.kind,
            title=self.title,
            content=self.content,
            deleted=self.deleted,
        )
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at or self.created_at,
            "created_by": self.created_by,
            "meta": self.meta,
            "source_memory_id": self.source_memory_id,
            "parent_hash": self.parent_hash,
            "content_hash": content_hash,
            "deleted": self.deleted,
        }

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "Entry":
        return cls(
            id=str(raw.get("id") or _new_id()),
            kind=str(raw.get("kind") or "learning"),
            title=str(raw.get("title") or ""),
            content=str(raw.get("content") or ""),
            created_at=str(raw.get("created_at") or _now_iso()),
            created_by=str(raw.get("created_by") or ""),
            meta=dict(raw.get("meta") or {}),
            source_memory_id=raw.get("source_memory_id") or None,
            parent_hash=raw.get("parent_hash") or None,
            content_hash=raw.get("content_hash") or None,
            updated_at=raw.get("updated_at") or None,
            deleted=bool(raw.get("deleted") or False),
        )


def _entry_hash(*, kind: str, title: str, content: str, deleted: bool) -> str:
    payload = {
        "kind": str(kind or ""),
        "title": str(title or ""),
        "content": str(content or ""),
        "deleted": bool(deleted),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _content_hash(entry: Entry) -> str:
    return entry.content_hash or _entry_hash(
        kind=entry.kind,
        title=entry.title,
        content=entry.content,
        deleted=entry.deleted,
    )


# ---------------------------------------------------------------------------
# Link registry — which repos has this machine linked?
# ---------------------------------------------------------------------------


def list_links() -> Dict[str, Dict[str, Any]]:
    raw = _read_json(_links_path(), {"repos": {}})
    repos = raw.get("repos") if isinstance(raw, dict) else None
    return repos if isinstance(repos, dict) else {}


def _save_links(repos: Dict[str, Dict[str, Any]]) -> None:
    _write_json(_links_path(), {"repos": repos})


# ---------------------------------------------------------------------------
# Workspace-store mirror so the UI sees linked repos automatically
# ---------------------------------------------------------------------------


def _mirror_workspace(repo_root: Path, *, shared: bool = True) -> None:
    state = _read_json(_workspace_store_path(), {"folders": {}})
    folders = state.get("folders") if isinstance(state, dict) else {}
    if not isinstance(folders, dict):
        folders = {}
    key = str(repo_root)
    folder = folders.get(key) or {}
    folder.update({
        "shared": bool(shared),
        "linked": True,
        "linked_at": folder.get("linked_at") or _now_iso(),
        "source": "dhee_link",
    })
    folders[key] = folder
    if not isinstance(state, dict):
        state = {}
    state.setdefault("schema_version", 2)
    state.setdefault("workspaces", {})
    state["folders"] = folders
    _write_json(_workspace_store_path(), state)


def _unmirror_workspace(repo_root: Path) -> None:
    state = _read_json(_workspace_store_path(), {"folders": {}})
    folders = state.get("folders") if isinstance(state, dict) else {}
    if not isinstance(folders, dict):
        return
    key = str(repo_root)
    if key in folders:
        # Don't delete user-configured folders — just clear our flag.
        folder = folders[key]
        if isinstance(folder, dict):
            folder.pop("linked", None)
            folder.pop("linked_at", None)
            folder.pop("source", None)
        if not folder:
            folders.pop(key, None)
        state["folders"] = folders
        _write_json(_workspace_store_path(), state)


# ---------------------------------------------------------------------------
# Repo-side files
# ---------------------------------------------------------------------------


def _read_repo_config(repo_root: Path) -> Dict[str, Any]:
    return _read_json(repo_config_path(repo_root), {}) or {}


def _write_repo_config(repo_root: Path, cfg: Dict[str, Any]) -> None:
    _write_json(repo_config_path(repo_root), cfg)


def _ensure_repo_skeleton(repo_root: Path) -> str:
    """Create ``<repo>/.dhee/`` and return the repo_id (existing or new)."""
    repo_dhee_dir(repo_root).mkdir(parents=True, exist_ok=True)
    repo_context_dir(repo_root).mkdir(parents=True, exist_ok=True)

    cfg = _read_repo_config(repo_root)
    if not cfg.get("repo_id"):
        cfg["repo_id"] = _new_id()
        cfg["schema_version"] = SCHEMA_VERSION
        cfg["linked_at"] = _now_iso()
        _write_repo_config(repo_root, cfg)

    entries = repo_entries_path(repo_root)
    if not entries.exists():
        entries.touch()

    # Treat entries.jsonl as a union-merge file: each user appends
    # their own lines; git's three-way merge concatenates without
    # spurious conflict markers.
    gattr = repo_dhee_dir(repo_root) / ".gitattributes"
    if not gattr.exists():
        gattr.write_text(
            "entries.jsonl merge=union\n",
            encoding="utf-8",
        )

    refresh_manifest(repo_root)
    return str(cfg["repo_id"])


def refresh_manifest(repo_root: Path) -> Dict[str, Any]:
    cfg = _read_repo_config(repo_root)
    repo_id = str(cfg.get("repo_id") or "")
    raw_entries = list(_iter_entries(repo_root))
    heads_by_id = _entry_heads_by_id(raw_entries)
    live = [
        _choose_entry_head(heads)
        for heads in heads_by_id.values()
        if heads and not _choose_entry_head(heads).deleted
    ]
    conflicts = _detect_conflicts_from_heads(heads_by_id)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "repo_id": repo_id,
        "entry_count": len(live),
        "tombstones": sum(1 for entries in heads_by_id.values() for entry in entries if entry.deleted),
        "conflicts": len(conflicts),
        "conflict_entry_ids": [item["entry_id"] for item in conflicts[:50]],
        "updated_at": _now_iso(),
    }
    _write_json(repo_manifest_path(repo_root), manifest)
    return manifest


def _iter_entries(repo_root: Path) -> Iterable[Entry]:
    path = repo_entries_path(repo_root)
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                yield Entry.from_json(raw)
    except OSError:
        return


def _append_entry(repo_root: Path, entry: Entry) -> None:
    path = repo_entries_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry.to_json(), separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _entry_heads(entries: List[Entry]) -> List[Entry]:
    if not entries:
        return []
    by_hash: Dict[str, Entry] = {}
    parent_hashes: set[str] = set()
    for entry in entries:
        h = _content_hash(entry)
        by_hash[h] = entry
        if entry.parent_hash:
            parent_hashes.add(str(entry.parent_hash))
    heads = [entry for h, entry in by_hash.items() if h not in parent_hashes]
    if not heads:
        heads = [entries[-1]]
    heads.sort(key=lambda e: (e.updated_at or e.created_at, _content_hash(e)))
    return heads


def _entry_heads_by_id(entries: List[Entry]) -> Dict[str, List[Entry]]:
    grouped: Dict[str, List[Entry]] = {}
    for entry in entries:
        grouped.setdefault(entry.id, []).append(entry)
    return {entry_id: _entry_heads(rows) for entry_id, rows in grouped.items()}


def _choose_entry_head(heads: List[Entry]) -> Entry:
    live = [entry for entry in heads if not entry.deleted]
    choices = live or heads
    return sorted(choices, key=lambda e: (e.updated_at or e.created_at, _content_hash(e)))[-1]


def _conflict_variants(heads: List[Entry]) -> List[Dict[str, Any]]:
    return [
        {
            "hash": _content_hash(entry),
            "title": entry.title,
            "kind": entry.kind,
            "created_by": entry.created_by,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at or entry.created_at,
            "deleted": entry.deleted,
        }
        for entry in heads
    ]


def _detect_conflicts_from_heads(heads_by_id: Dict[str, List[Entry]]) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    for entry_id, heads in heads_by_id.items():
        semantic_hashes = {_content_hash(entry) for entry in heads}
        if len(semantic_hashes) <= 1:
            continue
        conflicts.append({
            "entry_id": entry_id,
            "head_count": len(semantic_hashes),
            "variants": _conflict_variants(heads),
        })
    conflicts.sort(key=lambda item: item["entry_id"])
    return conflicts


def _with_conflict_meta(entry: Entry, heads: List[Entry]) -> Entry:
    semantic_hashes = {_content_hash(head) for head in heads}
    if len(semantic_hashes) <= 1:
        return entry
    raw = entry.to_json()
    meta = dict(raw.get("meta") or {})
    meta["dhee_conflict"] = {
        "entry_id": entry.id,
        "head_count": len(semantic_hashes),
        "variants": _conflict_variants(heads),
        "resolution": "manual_review_required",
    }
    raw["meta"] = meta
    return Entry.from_json(raw)


def list_entries(
    repo_root: Path, *, include_deleted: bool = False
) -> List[Entry]:
    """Return one current head per entry id, marking divergent heads.

    The repo context file is append-only and Git-union-merged. When two
    developers edit the same entry from the same parent, both update lines
    survive the pull. Dhee must not silently choose a winner, so the returned
    head carries ``meta.dhee_conflict`` until a context manager resolves it.
    """
    heads_by_id = _entry_heads_by_id(list(_iter_entries(repo_root)))
    out = [_with_conflict_meta(_choose_entry_head(heads), heads) for heads in heads_by_id.values() if heads]
    if not include_deleted:
        out = [e for e in out if not e.deleted]
    out.sort(key=lambda e: e.updated_at or e.created_at)
    return out


def get_entry(repo_root: Path, entry_id: str) -> Optional[Entry]:
    entries = [entry for entry in _iter_entries(repo_root) if entry.id == entry_id]
    heads = _entry_heads(entries)
    if not heads:
        return None
    return _with_conflict_meta(_choose_entry_head(heads), heads)


def detect_conflicts(repo_root: Path) -> List[Dict[str, Any]]:
    """Return divergent context heads created by Git union merges."""
    return _detect_conflicts_from_heads(_entry_heads_by_id(list(_iter_entries(repo_root))))


def check(repo: str | os.PathLike[str] | None = None) -> Dict[str, Any]:
    """Refresh and report whether a repo's shared context is safe to push."""
    repo_root = _resolve_repo(repo)
    if repo_root is None:
        raise ValueError("No linked repo for cwd. Pass --repo, or run `dhee link` first.")
    manifest = refresh_manifest(repo_root)
    conflicts = detect_conflicts(repo_root)
    return {
        "ok": not conflicts,
        "repo_root": str(repo_root),
        "manifest": manifest,
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# Git hooks — refresh on pull/checkout/rebase
# ---------------------------------------------------------------------------


_HOOK_NAMES = ("post-merge", "post-checkout", "post-rewrite", "pre-push")
_HOOK_MARKER = "# dhee-managed"


def _hook_body(repo_root: Path, name: str) -> str:
    if name == "pre-push":
        return (
            "#!/bin/sh\n"
            f"{_HOOK_MARKER}\n"
            "# Prevents pushing divergent Dhee shared-context heads.\n"
            f'dhee context check --repo "{repo_root}" --quiet >/dev/null 2>&1\n'
            "status=$?\n"
            'if [ "$status" -ne 0 ]; then\n'
            '  echo "Dhee shared context has unresolved conflicts. Run: dhee context check --repo '
            f"'{repo_root}'" + '" >&2\n'
            "  exit $status\n"
            "fi\n"
        )
    return (
        "#!/bin/sh\n"
        f"{_HOOK_MARKER}\n"
        "# Refreshes Dhee repo-context after a git update. Safe to remove.\n"
        f'dhee context refresh --repo "{repo_root}" --quiet >/dev/null 2>&1 || true\n'
    )


def _hooks_dir(repo_root: Path) -> Optional[Path]:
    git = repo_root / ".git"
    if git.is_dir():
        return git / "hooks"
    if git.is_file():
        # git worktree: .git is a file pointing at the real gitdir.
        try:
            text = git.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in text.splitlines():
            if line.startswith("gitdir:"):
                gitdir = Path(line.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = (repo_root / gitdir).resolve()
                return gitdir / "hooks"
    return None


def install_hooks(repo_root: Path) -> List[str]:
    """Install/refresh dhee git hooks. Returns the names installed.

    If a user-authored hook already exists at the same name, we
    preserve it: the user's hook becomes ``<name>.user`` and our hook
    delegates to it before refreshing context. Idempotent — running
    twice is a no-op.
    """
    hooks_dir = _hooks_dir(repo_root)
    if hooks_dir is None:
        return []
    hooks_dir.mkdir(parents=True, exist_ok=True)

    installed: List[str] = []
    for name in _HOOK_NAMES:
        body = _hook_body(repo_root, name)
        hook = hooks_dir / name
        if hook.exists():
            existing = hook.read_text(encoding="utf-8", errors="replace")
            if _HOOK_MARKER in existing:
                # Already ours — refresh in case the path changed.
                hook.write_text(body, encoding="utf-8")
                hook.chmod(0o755)
                installed.append(name)
                continue
            # User hook present — preserve it and chain.
            user_copy = hooks_dir / f"{name}.user"
            if not user_copy.exists():
                user_copy.write_text(existing, encoding="utf-8")
                user_copy.chmod(0o755)
            chained = (
                "#!/bin/sh\n"
                f"{_HOOK_MARKER}\n"
                f'"{user_copy}" "$@"\n'
                "status=$?\n"
                + (
                    'if [ "$status" -ne 0 ]; then exit "$status"; fi\n'
                    if name == "pre-push"
                    else ""
                )
                + (
                    f'dhee context check --repo "{repo_root}" --quiet >/dev/null 2>&1 || exit $?\n'
                    if name == "pre-push"
                    else f'dhee context refresh --repo "{repo_root}" --quiet >/dev/null 2>&1 || true\n'
                )
            )
            hook.write_text(chained, encoding="utf-8")
            hook.chmod(0o755)
            installed.append(name)
        else:
            hook.write_text(body, encoding="utf-8")
            hook.chmod(0o755)
            installed.append(name)
    return installed


def uninstall_hooks(repo_root: Path) -> List[str]:
    hooks_dir = _hooks_dir(repo_root)
    if hooks_dir is None or not hooks_dir.exists():
        return []
    removed: List[str] = []
    for name in _HOOK_NAMES:
        hook = hooks_dir / name
        if not hook.exists():
            continue
        try:
            text = hook.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _HOOK_MARKER not in text:
            continue
        user_copy = hooks_dir / f"{name}.user"
        if user_copy.exists():
            # Restore the original.
            try:
                user_text = user_copy.read_text(encoding="utf-8")
                hook.write_text(user_text, encoding="utf-8")
                hook.chmod(0o755)
                user_copy.unlink()
            except OSError:
                pass
        else:
            try:
                hook.unlink()
            except OSError:
                continue
        removed.append(name)
    return removed


# ---------------------------------------------------------------------------
# Public link API
# ---------------------------------------------------------------------------


def link(path: str | os.PathLike[str] = ".") -> Dict[str, Any]:
    """Link a git repository to this machine.

    Side-effects (all idempotent):

    * Resolves *path* to its git root.
    * Creates ``<repo>/.dhee/`` skeleton with ``config.json``,
      ``context/manifest.json``, ``context/entries.jsonl``,
      ``.gitattributes``.
    * Registers the repo in ``~/.dhee/links.json``.
    * Mirrors the repo into ``~/.dhee/local_context_folders.json``
      (the existing UI workspace store) so the canvas finds it.
    * Installs refresh hooks plus a ``pre-push`` conflict check.
    """
    target = _resolve(path)
    repo_root = _git_top(target)
    if repo_root is None:
        raise ValueError(
            f"{target} is not inside a git repository. "
            "Run `git init` first or pass a path inside a checked-out repo."
        )

    repo_id = _ensure_repo_skeleton(repo_root)
    hooks = install_hooks(repo_root)
    _mirror_workspace(repo_root, shared=True)

    repos = list_links()
    repos[str(repo_root)] = {
        "repo_id": repo_id,
        "linked_at": repos.get(str(repo_root), {}).get("linked_at") or _now_iso(),
        "hooks_installed": bool(hooks),
    }
    _save_links(repos)

    return {
        "repo_root": str(repo_root),
        "repo_id": repo_id,
        "hooks": hooks,
        "manifest": _read_json(repo_manifest_path(repo_root), {}),
    }


def unlink(path: str | os.PathLike[str] = ".", *, remove_hooks: bool = True) -> Dict[str, Any]:
    """Remove this repo from the machine's link registry.

    Does *not* delete ``<repo>/.dhee/`` — that's git-tracked content
    owned by the repo. Only the per-machine pointer and (optionally)
    the git hooks come off.
    """
    target = _resolve(path)
    repo_root = _git_top(target) or target

    repos = list_links()
    removed = repos.pop(str(repo_root), None)
    _save_links(repos)
    _unmirror_workspace(repo_root)

    hooks: List[str] = []
    if remove_hooks:
        hooks = uninstall_hooks(repo_root)

    return {
        "repo_root": str(repo_root),
        "removed": removed,
        "hooks_removed": hooks,
    }


def repo_for_path(path: str | os.PathLike[str]) -> Optional[Path]:
    """Return the linked-repo root that contains *path*, or ``None``.

    Picks the deepest match so nested checkouts behave sanely.
    """
    target = _resolve(path)
    best: Optional[Path] = None
    best_len = -1
    for root_str in list_links().keys():
        root = Path(root_str)
        if _path_within(target, root) and len(str(root)) > best_len:
            best = root
            best_len = len(str(root))
    return best


def refresh(repo: str | os.PathLike[str] | None = None) -> List[Dict[str, Any]]:
    """Recompute manifest(s) and re-mirror into the UI workspace store.

    Called by the post-merge / post-checkout / post-rewrite hook after
    a teammate's pull, and by ``dhee context refresh``.
    """
    targets: List[Path] = []
    if repo is not None:
        target = _resolve(repo)
        root = _git_top(target) or target
        targets.append(root)
    else:
        targets = [Path(p) for p in list_links().keys()]

    out: List[Dict[str, Any]] = []
    for root in targets:
        if not root.exists():
            continue
        if not repo_dhee_dir(root).exists():
            # Linked repo lost its .dhee/ — recreate the skeleton so
            # subsequent writes don't fail.
            _ensure_repo_skeleton(root)
        manifest = refresh_manifest(root)
        _mirror_workspace(root, shared=True)
        out.append({"repo_root": str(root), "manifest": manifest})
    return out


# ---------------------------------------------------------------------------
# Promote / Demote
# ---------------------------------------------------------------------------


def _machine_id() -> str:
    return os.environ.get("DHEE_USER_ID") or os.environ.get("USER") or "unknown"


def add_entry(
    repo_root: Path,
    *,
    kind: str,
    title: str,
    content: str,
    meta: Optional[Dict[str, Any]] = None,
    source_memory_id: Optional[str] = None,
) -> Entry:
    """Append a new entry to ``<repo>/.dhee/context/entries.jsonl``
    and refresh the manifest. Plumbing for promote() and any future
    auto-distiller."""
    entry = Entry(
        id=_new_id(),
        kind=kind,
        title=title.strip(),
        content=content,
        created_at=_now_iso(),
        created_by=_machine_id(),
        meta=dict(meta or {}),
        source_memory_id=source_memory_id,
    )
    _ensure_repo_skeleton(repo_root)
    _append_entry(repo_root, entry)
    refresh_manifest(repo_root)
    return entry


def update_entry(
    repo_root: Path,
    entry_id: str,
    *,
    title: Optional[str] = None,
    content: Optional[str] = None,
    kind: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    expected_hash: Optional[str] = None,
) -> Entry:
    """Append a versioned update to a repo-context entry.

    ``expected_hash`` is the optimistic-concurrency guard used by UI/API
    callers. If a teammate changed the same entry after the caller read it,
    this raises instead of writing a stale overwrite.
    """
    conflicts = [c for c in detect_conflicts(repo_root) if c.get("entry_id") == entry_id]
    if conflicts:
        raise ContextConflictError(
            entry_id,
            f"Entry {entry_id!r} has divergent Git-merged context heads.",
            conflicts=conflicts,
        )
    existing = get_entry(repo_root, entry_id)
    if existing is None or existing.deleted:
        raise ValueError(f"Entry {entry_id!r} not found.")
    current_hash = _content_hash(existing)
    if expected_hash and expected_hash != current_hash:
        raise ContextConflictError(
            entry_id,
            f"Entry {entry_id!r} changed before this update could be saved.",
            conflicts=[{
                "entry_id": entry_id,
                "expected_hash": expected_hash,
                "current_hash": current_hash,
            }],
        )
    now = _now_iso()
    merged_meta = dict(existing.meta or {})
    merged_meta.update(meta or {})
    merged_meta["updated_by"] = _machine_id()
    updated = Entry(
        id=existing.id,
        kind=(kind if kind is not None else existing.kind),
        title=(title if title is not None else existing.title).strip(),
        content=(content if content is not None else existing.content),
        created_at=existing.created_at,
        updated_at=now,
        created_by=existing.created_by,
        meta=merged_meta,
        source_memory_id=existing.source_memory_id,
        parent_hash=current_hash,
    )
    _ensure_repo_skeleton(repo_root)
    _append_entry(repo_root, updated)
    refresh_manifest(repo_root)
    return updated


def tombstone_entry(repo_root: Path, entry_id: str) -> Optional[Entry]:
    conflicts = [c for c in detect_conflicts(repo_root) if c.get("entry_id") == entry_id]
    if conflicts:
        raise ContextConflictError(
            entry_id,
            f"Entry {entry_id!r} has divergent Git-merged context heads.",
            conflicts=conflicts,
        )
    existing = get_entry(repo_root, entry_id)
    if existing is None or existing.deleted:
        return None
    tomb = Entry(
        id=existing.id,
        kind=existing.kind,
        title=existing.title,
        content=existing.content,
        created_at=existing.created_at,
        created_by=existing.created_by,
        meta=existing.meta,
        source_memory_id=existing.source_memory_id,
        parent_hash=_content_hash(existing),
        updated_at=_now_iso(),
        deleted=True,
    )
    _append_entry(repo_root, tomb)
    refresh_manifest(repo_root)
    return tomb


def promote(
    memory_id: str,
    *,
    memory: Any | None = None,
    repo: str | os.PathLike[str] | None = None,
    kind: str = "learning",
    title: Optional[str] = None,
) -> Tuple[Entry, Path]:
    """Copy a personal memory into a linked repo's shared context.

    Default repo target is the linked repo containing ``cwd``. Pass
    ``repo`` to force a target. Returns the new entry and the repo
    root it was written into.
    """
    repo_root = _resolve_repo(repo)
    if repo_root is None:
        raise ValueError(
            "No linked repo for cwd. Pass --repo, or run `dhee link` "
            "inside the target repo first."
        )

    if memory is None:
        from dhee.cli_config import get_memory_instance
        memory = get_memory_instance(None)

    record = memory.get(memory_id)
    if not record:
        raise ValueError(f"Memory {memory_id!r} not found.")

    body = (
        record.get("memory")
        or record.get("details")
        or record.get("content")
        or ""
    )
    if not body:
        raise ValueError(f"Memory {memory_id!r} has no content to promote.")

    meta = dict(record.get("metadata") or {})
    inferred_title = (str(body).strip().splitlines() or [""])[0][:120]
    entry = add_entry(
        repo_root,
        kind=kind,
        title=(title or inferred_title or memory_id),
        content=str(body),
        meta={
            "categories": record.get("categories") or [],
            "layer": record.get("layer"),
            "promoted_from_memory": memory_id,
            "promoted_at": _now_iso(),
            "original_metadata": meta,
        },
        source_memory_id=memory_id,
    )

    # Stamp the personal memory so we can show "promoted to <repo>" in
    # the UI / CLI later, and avoid re-promoting the same thing.
    try:
        new_meta = dict(meta)
        promoted_to = list(new_meta.get("promoted_to") or [])
        promoted_to.append({
            "repo_root": str(repo_root),
            "entry_id": entry.id,
            "at": entry.created_at,
        })
        new_meta["promoted_to"] = promoted_to
        memory.db.update_memory(memory_id, {"metadata": new_meta})
    except Exception:
        # Annotation is a nicety, not a contract.
        pass

    return entry, repo_root


def demote(
    entry_id: str,
    *,
    memory: Any | None = None,
    repo: str | os.PathLike[str] | None = None,
    user_id: str = "default",
) -> Tuple[str, Entry]:
    """Copy a repo entry into personal memory as a learning. The repo
    entry stays in place — demote *adds* a personal copy, it does not
    move the entry."""
    repo_root = _resolve_repo(repo)
    if repo_root is None:
        raise ValueError(
            "No linked repo for cwd. Pass --repo, or run `dhee link` first."
        )

    entry = get_entry(repo_root, entry_id)
    if entry is None:
        raise ValueError(f"Entry {entry_id!r} not found in {repo_root}.")
    if entry.deleted:
        raise ValueError(f"Entry {entry_id!r} is tombstoned.")

    if memory is None:
        from dhee.cli_config import get_memory_instance
        memory = get_memory_instance(None)

    result = memory.add(
        messages=entry.content,
        user_id=user_id,
        metadata={
            "demoted_from_repo": str(repo_root),
            "demoted_from_entry": entry.id,
            "demoted_at": _now_iso(),
            "kind": entry.kind,
            "title": entry.title,
            "source": "repo_demote",
        },
        infer=False,
    )

    new_id = ""
    if isinstance(result, dict):
        results = result.get("results") or []
        if results and isinstance(results, list):
            first = results[0] or {}
            new_id = str(first.get("id") or first.get("memory_id") or "")
    return new_id, entry


def _resolve_repo(repo: str | os.PathLike[str] | None) -> Optional[Path]:
    if repo is not None:
        target = _resolve(repo)
        root = _git_top(target) or target
        return root
    return repo_for_path(Path.cwd())


# ---------------------------------------------------------------------------
# Read-time fusion: keyword-rank entries from a linked repo
# ---------------------------------------------------------------------------


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for",
    "is", "are", "was", "were", "be", "been", "this", "that", "with",
    "as", "by", "at", "it", "from",
})


def _tokens(text: str) -> List[str]:
    out: List[str] = []
    cur: List[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch == "_":
            cur.append(ch)
        else:
            if cur:
                tok = "".join(cur)
                if len(tok) > 1 and tok not in _STOPWORDS:
                    out.append(tok)
                cur.clear()
    if cur:
        tok = "".join(cur)
        if len(tok) > 1 and tok not in _STOPWORDS:
            out.append(tok)
    return out


def search_entries(
    query: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    repo: str | os.PathLike[str] | None = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Keyword-rank repo-context entries for *query*.

    If *repo* is omitted, picks the linked repo containing *cwd*
    (default ``cwd=$PWD``). Returns dicts shaped close enough to
    ``Memory.search`` results that fusion is one ``extend()`` call.
    """
    if repo is not None:
        repo_root = _resolve_repo(repo)
    else:
        target = Path(cwd) if cwd else Path.cwd()
        repo_root = repo_for_path(target)
    if repo_root is None:
        return []

    q_tokens = _tokens(query)
    if not q_tokens:
        return []
    q_set = set(q_tokens)

    scored: List[Tuple[float, Entry]] = []
    for entry in list_entries(repo_root):
        body = f"{entry.title}\n{entry.content}"
        toks = _tokens(body)
        if not toks:
            continue
        # Score = unique-overlap (recall) + frequency (precision boost).
        overlap = len(q_set.intersection(toks))
        if not overlap:
            continue
        freq = sum(1 for t in toks if t in q_set)
        score = overlap + 0.05 * freq
        scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    out: List[Dict[str, Any]] = []
    for score, entry in scored[: max(1, int(limit))]:
        out.append({
            "id": entry.id,
            "memory": entry.content,
            "title": entry.title,
            "kind": entry.kind,
            "score": round(float(score), 3),
            "composite_score": round(float(score), 3),
            "source": "repo",
            "repo_root": str(repo_root),
            "created_at": entry.created_at,
            "created_by": entry.created_by,
            "metadata": dict(entry.meta),
            "layer": "repo",
        })
    return out


def fuse_search_results(
    query: str,
    base_results: Iterable[Dict[str, Any]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Merge personal memory results with linked-repo entries.

    Every personal result keeps its existing shape and gains
    ``source='personal'`` if absent. Repo entries arrive via
    ``search_entries`` and carry ``source='repo'``. Sort is by
    ``composite_score`` desc, ties broken by personal-first so a
    user's own memory wins over a teammate's note at equal score.
    """
    personal: List[Dict[str, Any]] = []
    for r in base_results:
        if not isinstance(r, dict):
            continue
        item = dict(r)
        item.setdefault("source", "personal")
        personal.append(item)

    repo = search_entries(query, cwd=cwd, limit=limit)

    def key(item: Dict[str, Any]) -> Tuple[float, int]:
        score = float(item.get("composite_score", item.get("score", 0)) or 0)
        # Stable tiebreak: personal beats repo on ties.
        rank = 0 if item.get("source") == "personal" else 1
        return (-score, rank)

    merged = personal + repo
    merged.sort(key=key)
    return merged[: max(1, int(limit))]
