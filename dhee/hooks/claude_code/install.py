"""Install Dhee hooks into Claude Code settings.

Writes hook entries into ``~/.claude/settings.json`` so that every Claude Code
session automatically gets Dhee cognition: typed-signal storage on tool use,
checkpoint on exit, and context injection at session start when typed
cognition exists. No markdown files, no SKILL.md, no plugins directory —
just Python hooks in the agent's native lifecycle.

v3.3.1 removes UserPromptSubmit from the default event set. The prior
per-turn injection created a pollution loop; see ``__main__.handle_user_prompt``
for the history. Existing UserPromptSubmit Dhee entries are migrated away
on next install/upgrade.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOOK_EVENTS: tuple[str, ...] = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PreCompact",
    "Stop",
    "SessionEnd",
)

# Events that Dhee owned in a prior version but no longer installs.
# Currently empty — UserPromptSubmit is back (doing doc-chunk retrieval,
# not raw-memory noise as in v3.3.0).
LEGACY_EVENTS: tuple[str, ...] = ()

TOOL_MATCHERS: dict[str, str] = {
    "PostToolUse": "Edit|Write|MultiEdit|Bash",
}

_DHEE_COMMAND_MARKER = "dhee.hooks.claude_code"


@dataclass
class InstallResult:
    settings_path: Path
    events: tuple[str, ...]
    created: bool = False
    updated: bool = False
    already_installed: bool = False
    backed_up: Path | None = None
    legacy_removed: tuple[str, ...] = ()
    # v3.3.0 → v3.3.1 DB cleanup: noisy memory entries removed. ``None``
    # when migration didn't run (fresh install or already-clean DB).
    noise_purged: int | None = None


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _python_cmd() -> str:
    return sys.executable or "python3"


def _build_entry(event: str) -> dict[str, Any]:
    cmd = f"{_python_cmd()} -m dhee.hooks.claude_code {event}"
    entry: dict[str, Any] = {
        "hooks": [{"type": "command", "command": cmd, "timeout": 10}],
    }
    if event in TOOL_MATCHERS:
        entry["matcher"] = TOOL_MATCHERS[event]
    return entry


def _has_dhee_hook(entries: list[dict[str, Any]]) -> bool:
    for entry in entries:
        for hook in entry.get("hooks", []):
            if _DHEE_COMMAND_MARKER in hook.get("command", ""):
                return True
    return False


def _all_installed(hooks: dict[str, Any], events: tuple[str, ...]) -> bool:
    for event in events:
        entries = hooks.get(event, [])
        if not isinstance(entries, list) or not _has_dhee_hook(entries):
            return False
    return True


def _strip_dhee_from_event(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``entries`` with Dhee-owned hook entries removed."""
    return [
        entry
        for entry in entries
        if not any(
            _DHEE_COMMAND_MARKER in hook.get("command", "")
            for hook in entry.get("hooks", [])
        )
    ]


def _remove_legacy_entries(
    hooks: dict[str, Any],
    legacy: tuple[str, ...] = LEGACY_EVENTS,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Strip Dhee hooks from events we no longer install.

    Returns (updated_hooks, tuple_of_events_we_cleaned). Non-Dhee entries
    in those events are preserved.
    """
    cleaned: list[str] = []
    for event in legacy:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        if not _has_dhee_hook(entries):
            continue
        remaining = _strip_dhee_from_event(entries)
        if remaining:
            hooks[event] = remaining
        else:
            del hooks[event]
        cleaned.append(event)
    return hooks, tuple(cleaned)


def install_hooks(
    *,
    force: bool = False,
    events: tuple[str, ...] = HOOK_EVENTS,
) -> InstallResult:
    """Install Dhee hooks into ``~/.claude/settings.json``.

    Also migrates any legacy Dhee hooks (e.g., UserPromptSubmit from v3.3.0)
    away so upgraders stop paying for no-op invocations.
    """
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict[str, Any] = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            settings = {}

    existing_hooks = settings.get("hooks", {}) or {}

    # Detect legacy Dhee hooks that need cleanup even when no install change
    # is otherwise required.
    _, pending_legacy = _remove_legacy_entries(dict(existing_hooks))
    fully_installed = _all_installed(existing_hooks, events)

    if not force and fully_installed and not pending_legacy:
        return InstallResult(
            settings_path=path,
            events=events,
            already_installed=True,
        )

    backed_up = None
    if path.exists():
        backup = path.with_suffix(".json.dhee-backup")
        shutil.copy2(path, backup)
        backed_up = backup

    hooks, legacy_removed = _remove_legacy_entries(dict(existing_hooks))

    for event in events:
        our_entry = _build_entry(event)
        if event in hooks and not force:
            existing = hooks[event]
            if isinstance(existing, list) and _has_dhee_hook(existing):
                continue
            if isinstance(existing, list):
                existing.append(our_entry)
            else:
                hooks[event] = [our_entry]
        else:
            hooks[event] = [our_entry]

    settings["hooks"] = hooks

    created = not path.exists()
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    # Upgrade-time DB cleanup: purge v3.3.0 noise from the vector store.
    # Only run when we're actually installing — a no-op install (everything
    # already in place, no legacy events) shouldn't scan the DB every time.
    noise_purged: int | None = None
    try:
        from dhee.hooks.claude_code.migrate import purge_legacy_noise

        result = purge_legacy_noise()
        noise_purged = result.removed
    except Exception:
        # Never let cleanup block an install.
        noise_purged = None

    return InstallResult(
        settings_path=path,
        events=events,
        created=created,
        updated=not created,
        backed_up=backed_up,
        legacy_removed=legacy_removed,
        noise_purged=noise_purged,
    )


def ensure_installed() -> InstallResult:
    """Install hooks if not already present. Also runs legacy cleanup."""
    return install_hooks()


def uninstall_hooks() -> bool:
    """Remove all Dhee hooks from settings.json, including legacy entries."""
    path = _settings_path()
    if not path.exists():
        return False

    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    hooks = settings.get("hooks", {})
    changed = False

    for event in list(hooks.keys()):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        filtered = _strip_dhee_from_event(entries)
        if len(filtered) != len(entries):
            changed = True
        if filtered:
            hooks[event] = filtered
        else:
            del hooks[event]

    if changed:
        settings["hooks"] = hooks
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    return changed
