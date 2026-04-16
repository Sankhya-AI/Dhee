"""Install Dhee hooks into Claude Code settings.

Writes hook entries into ``~/.claude/settings.json`` so that every Claude Code
session automatically gets Dhee cognition: memory injection on start, learning
on tool use, checkpoint on exit. No markdown files, no SKILL.md, no plugins
directory — just Python hooks in the agent's native lifecycle.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
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

TOOL_MATCHERS: dict[str, str] = {
    "PostToolUse": "Edit|Write|MultiEdit|Bash",
}


@dataclass
class InstallResult:
    settings_path: Path
    events: tuple[str, ...]
    created: bool = False
    updated: bool = False
    already_installed: bool = False
    backed_up: Path | None = None


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
            if "dhee.hooks.claude_code" in hook.get("command", ""):
                return True
    return False


def _all_installed(hooks: dict[str, Any], events: tuple[str, ...]) -> bool:
    for event in events:
        entries = hooks.get(event, [])
        if not isinstance(entries, list) or not _has_dhee_hook(entries):
            return False
    return True


def install_hooks(
    *,
    force: bool = False,
    events: tuple[str, ...] = HOOK_EVENTS,
) -> InstallResult:
    """Install Dhee hooks into ``~/.claude/settings.json``."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict[str, Any] = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            settings = {}

    existing_hooks = settings.get("hooks", {})

    if not force and _all_installed(existing_hooks, events):
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

    hooks = dict(existing_hooks)
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

    return InstallResult(
        settings_path=path,
        events=events,
        created=created,
        updated=not created,
        backed_up=backed_up,
    )


def ensure_installed() -> InstallResult:
    """Install hooks if not already present."""
    return install_hooks()


def uninstall_hooks() -> bool:
    """Remove Dhee hooks from settings.json."""
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
        filtered = [
            e for e in entries
            if not any("dhee.hooks.claude_code" in h.get("command", "") for h in e.get("hooks", []))
        ]
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
