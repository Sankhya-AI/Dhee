"""Router enable/disable for Claude Code.

Surgical edits to ``~/.claude/settings.json``:

- adds ``mcp__dhee__dhee_read`` / ``dhee_bash`` / ``dhee_expand_result``
  to ``permissions.allow`` so the model can call them without prompts
- sets ``DHEE_ROUTER=1`` env on the Dhee MCP server so the SessionStart
  hook injects the router nudge
- marks its additions with ``_dhee_router_managed`` so they round-trip
  out cleanly on disable

Guarantees:

- Atomic: writes to tempfile then renames
- Reversible: backup written to ``settings.json.dhee-router-backup``
- Idempotent: re-enable is a no-op when already enabled
- Non-destructive: never touches keys Dhee doesn't own (model, other MCP
  servers' env, unrelated hooks)
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROUTER_TOOLS = (
    "mcp__dhee__dhee_read",
    "mcp__dhee__dhee_bash",
    "mcp__dhee__dhee_agent",
    "mcp__dhee__dhee_expand_result",
)

ENV_FLAG = "DHEE_ROUTER"
ENFORCE_FLAG = "DHEE_ROUTER_ENFORCE"
MANAGED_MARKER = "_dhee_router_managed"


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


@dataclass
class RouterState:
    settings_path: Path
    enabled: bool = False
    allowed_tools: list[str] = field(default_factory=list)
    env_flag: str | None = None
    managed: bool = False


@dataclass
class RouterInstallResult:
    settings_path: Path
    action: str  # "enabled", "already_enabled", "disabled", "already_disabled"
    added_allows: list[str] = field(default_factory=list)
    removed_allows: list[str] = field(default_factory=list)
    env_flag_set: bool = False
    env_flag_cleared: bool = False
    backed_up: Path | None = None
    hooks_installed: bool = False
    enforce_turned_on: bool = False
    enforce_turned_off: bool = False


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _backup(path: Path, tag: str) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_suffix(f".json.{tag}-backup")
    shutil.copy2(path, backup)
    return backup


def status() -> RouterState:
    """Report current router state (no side effects)."""
    path = _settings_path()
    settings = _load_settings(path)

    perms = settings.get("permissions") or {}
    allow = perms.get("allow") if isinstance(perms, dict) else None
    allow_list = [a for a in (allow or []) if isinstance(a, str)]

    mcp = settings.get("mcpServers") or {}
    dhee_server = mcp.get("dhee") if isinstance(mcp, dict) else None
    env = dhee_server.get("env") if isinstance(dhee_server, dict) else {}
    flag = (env or {}).get(ENV_FLAG)

    managed = bool(perms.get(MANAGED_MARKER)) if isinstance(perms, dict) else False

    allowed_present = [t for t in ROUTER_TOOLS if t in allow_list]
    enabled = bool(allowed_present) and str(flag) == "1"

    return RouterState(
        settings_path=path,
        enabled=enabled,
        allowed_tools=allowed_present,
        env_flag=str(flag) if flag is not None else None,
        managed=managed,
    )


def enable(
    *,
    install_hooks: bool = True,
    enforce: bool = True,
) -> RouterInstallResult:
    """Enable the Dhee router in Claude Code settings. Idempotent.

    The default is *full-strength*: install hooks (so PreToolUse
    enforcement actually fires), add router tool permissions, set
    ``DHEE_ROUTER=1`` on the Dhee MCP server, and create the enforce
    flag file so the PreToolUse gate denies native Read/Bash on heavy
    tool calls. Pass ``install_hooks=False`` or ``enforce=False`` to
    opt out individually — used by tests and advanced setups.
    """
    path = _settings_path()

    # Hook install is orthogonal to the router env flag; run it first so
    # that PreToolUse is present by the time we enable enforcement.
    hooks_installed = False
    if install_hooks:
        try:
            from dhee.hooks.claude_code import install as _hook_install

            hres = _hook_install.ensure_installed()
            hooks_installed = bool(hres.created or hres.updated)
        except Exception:
            # Never let hook install block router enable — the MCP nudge
            # path still works; caller will see hooks_installed=False.
            hooks_installed = False

    settings = _load_settings(path)

    current = status()
    already = current.enabled and all(t in current.allowed_tools for t in ROUTER_TOOLS)

    enforce_turned_on = False
    if enforce:
        try:
            from dhee.router.pre_tool_gate import _flag_file

            flag = _flag_file()
            flag.parent.mkdir(parents=True, exist_ok=True)
            if not flag.exists():
                flag.write_text("1\n", encoding="utf-8")
                enforce_turned_on = True
        except Exception:
            enforce_turned_on = False

    if already and not enforce_turned_on and not hooks_installed:
        return RouterInstallResult(
            settings_path=path,
            action="already_enabled",
            added_allows=[],
            env_flag_set=False,
        )

    backed_up = _backup(path, "dhee-router") if not already else None

    perms = settings.get("permissions")
    if not isinstance(perms, dict):
        perms = {}
    allow = perms.get("allow")
    if not isinstance(allow, list):
        allow = []

    added: list[str] = []
    for tool in ROUTER_TOOLS:
        if tool not in allow:
            allow.append(tool)
            added.append(tool)

    perms["allow"] = allow
    perms[MANAGED_MARKER] = True
    settings["permissions"] = perms

    mcp = settings.get("mcpServers")
    env_flag_set = False
    if isinstance(mcp, dict) and "dhee" in mcp and isinstance(mcp["dhee"], dict):
        env = mcp["dhee"].get("env")
        if not isinstance(env, dict):
            env = {}
        if env.get(ENV_FLAG) != "1":
            env[ENV_FLAG] = "1"
            env_flag_set = True
        mcp["dhee"]["env"] = env
        settings["mcpServers"] = mcp

    # Only rewrite settings.json if something in it actually changed.
    if added or env_flag_set:
        _atomic_write(path, settings)

    return RouterInstallResult(
        settings_path=path,
        action="enabled",
        added_allows=added,
        env_flag_set=env_flag_set,
        backed_up=backed_up,
        hooks_installed=hooks_installed,
        enforce_turned_on=enforce_turned_on,
    )


def disable() -> RouterInstallResult:
    """Remove router permissions + env flag + enforce flag.

    Leaves the MCP server block and hook registrations in place —
    hooks become no-ops once ``DHEE_ROUTER`` is unset, and the MCP
    server is still useful for the 4 cognition tools. Rollback SLA:
    this call should complete in well under a second; restart Claude
    Code to pick it up.
    """
    path = _settings_path()

    # Clear the enforce flag unconditionally — it lives outside
    # settings.json and is the primary kill-switch for the PreToolUse
    # gate. Doing it first guarantees rollback even if settings.json
    # write fails mid-way.
    enforce_turned_off = False
    try:
        from dhee.router.pre_tool_gate import _flag_file

        flag = _flag_file()
        if flag.exists():
            flag.unlink()
            enforce_turned_off = True
    except Exception:
        enforce_turned_off = False

    settings = _load_settings(path)
    if not settings:
        return RouterInstallResult(
            settings_path=path,
            action="already_disabled" if not enforce_turned_off else "disabled",
            enforce_turned_off=enforce_turned_off,
        )

    backed_up = _backup(path, "dhee-router")

    removed: list[str] = []
    perms = settings.get("permissions")
    if isinstance(perms, dict):
        allow = perms.get("allow")
        if isinstance(allow, list):
            kept = []
            for a in allow:
                if isinstance(a, str) and a in ROUTER_TOOLS:
                    removed.append(a)
                else:
                    kept.append(a)
            if kept:
                perms["allow"] = kept
            else:
                perms.pop("allow", None)
        perms.pop(MANAGED_MARKER, None)
        if not perms:
            settings.pop("permissions", None)
        else:
            settings["permissions"] = perms

    env_flag_cleared = False
    mcp = settings.get("mcpServers")
    if isinstance(mcp, dict) and isinstance(mcp.get("dhee"), dict):
        env = mcp["dhee"].get("env")
        if isinstance(env, dict) and ENV_FLAG in env:
            env.pop(ENV_FLAG, None)
            env_flag_cleared = True
            if env:
                mcp["dhee"]["env"] = env
            else:
                mcp["dhee"].pop("env", None)

    if not removed and not env_flag_cleared:
        return RouterInstallResult(
            settings_path=path,
            action="disabled" if enforce_turned_off else "already_disabled",
            enforce_turned_off=enforce_turned_off,
        )

    _atomic_write(path, settings)

    return RouterInstallResult(
        settings_path=path,
        action="disabled",
        removed_allows=removed,
        env_flag_cleared=env_flag_cleared,
        backed_up=backed_up,
        enforce_turned_off=enforce_turned_off,
    )
