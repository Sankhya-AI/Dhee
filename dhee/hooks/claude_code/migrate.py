"""Migration helpers for v3.3.0 → v3.3.1.

v3.3.0 PostToolUse stored every Bash invocation verbatim, polluting the
vector store with shell-command echoes. v3.3.1 only stores failures and
file-edit events. Existing installs will carry the legacy noise forever
unless we purge it on upgrade.

This module provides:

- ``purge_legacy_noise`` — scan the Dhee memory store for entries written
  by the v3.3.0 hook (``source == "claude_code_hook"`` with a Bash-success
  shape, or self-referential commands under any metadata) and delete them.
  Returns the count removed. Never raises — a failed purge should not
  block the upgrade.

The function is invoked by ``install_hooks`` at the end of a real install
so upgrading is one command. It is also exposed as a CLI subcommand
(``dhee purge-legacy-noise``) so a user can re-run it after the fact.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dhee.hooks.claude_code.signal import is_self_referential


@dataclass
class PurgeResult:
    scanned: int = 0
    removed: int = 0
    db_path: Path | None = None
    skipped_reason: str | None = None


def _default_db_path() -> Path:
    return Path.home() / ".dhee" / "sqlite_vec.db"


def _looks_like_legacy_bash_success(entry: dict) -> bool:
    """True when an entry was written by the v3.3.0 PostToolUse on a
    successful Bash invocation (exactly the shape the new code no longer
    writes)."""
    if entry.get("source") != "claude_code_hook":
        return False
    if entry.get("tool") != "Bash":
        return False
    if entry.get("success") is not True:
        return False
    return True


def _looks_like_self_referential(entry: dict) -> bool:
    """True when the stored text invokes Dhee internals or the hook harness.

    Applied regardless of metadata — a self-referential command is always
    pollution, whether it came from the hook or a direct ``remember()``.
    """
    text = entry.get("text") or entry.get("memory") or entry.get("content") or ""
    if not isinstance(text, str) or not text:
        return False
    # Drop the "ran: " prefix we added so the pattern matches against the
    # command itself, not the wrapper.
    stripped = text.removeprefix("ran: ").strip()
    if not stripped:
        return False
    return is_self_referential(stripped)


def purge_legacy_noise(
    db_path: Path | None = None,
    *,
    dry_run: bool = False,
) -> PurgeResult:
    """Remove v3.3.0 noise entries from the Dhee vector store.

    Safe to run multiple times. Idempotent.
    """
    path = db_path or _default_db_path()
    if not path.exists():
        return PurgeResult(db_path=path, skipped_reason="db_missing")

    try:
        con = sqlite3.connect(str(path))
    except sqlite3.Error as exc:
        return PurgeResult(db_path=path, skipped_reason=f"open_failed:{exc}")

    try:
        # Schema introspection — bail if the tables we expect aren't there.
        names = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "payload_dhee" not in names:
            return PurgeResult(db_path=path, skipped_reason="no_payload_table")

        rows = con.execute(
            "SELECT rowid, uuid, payload FROM payload_dhee"
        ).fetchall()
        scanned = len(rows)

        to_remove: list[tuple[int, str]] = []
        for rowid, uuid, payload in rows:
            try:
                entry = json.loads(payload) if payload else {}
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(entry, dict):
                continue
            if _looks_like_legacy_bash_success(entry) or _looks_like_self_referential(entry):
                to_remove.append((rowid, uuid))

        if dry_run:
            return PurgeResult(scanned=scanned, removed=len(to_remove), db_path=path)
        if not to_remove:
            return PurgeResult(scanned=scanned, removed=0, db_path=path)

        con.execute("BEGIN")
        for rowid, uuid in to_remove:
            con.execute("DELETE FROM payload_dhee WHERE rowid = ?", (rowid,))
            # Companion tables keyed on the same uuid/rowid — clean them too
            # if present. Unknown tables are tolerated.
            for companion in ("vec_dhee_chunks", "vec_dhee", "vec_dhee_info", "vec_dhee_rowids"):
                if companion not in names:
                    continue
                try:
                    cols = {r[1] for r in con.execute(f"PRAGMA table_info({companion})")}
                    if "uuid" in cols:
                        con.execute(f"DELETE FROM {companion} WHERE uuid = ?", (uuid,))
                    elif "rowid" in cols or companion.endswith("rowids"):
                        con.execute(f"DELETE FROM {companion} WHERE rowid = ?", (rowid,))
                except sqlite3.Error:
                    # Companion cleanup is best-effort; the payload delete
                    # is the authoritative action.
                    pass
        con.commit()

        return PurgeResult(scanned=scanned, removed=len(to_remove), db_path=path)
    finally:
        con.close()
