"""One-shot Claude Code bootstrap for Dhee.

Wires up, in the canonical ``~/.claude/settings.json``:

1. Hooks (SessionStart/PostToolUse/PreCompact/Stop/SessionEnd/UserPromptSubmit)
2. The ``dhee`` MCP server registration
3. Router permissions + DHEE_ROUTER=1 env
4. PreToolUse enforcement flag (``~/.dhee/router_enforce``) — default on

Everything is idempotent, atomic, and reversible. Used by the curl-one-line
installer and by ``dhee install``. Enforcement steers native Read/Bash/Grep
onto the router; opt out with ``DHEE_ROUTER_ENFORCE=0`` or ``dhee router disable``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


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


def _dhee_mcp_binary() -> str:
    """Find the installed ``dhee-mcp`` entry point.

    Prefers a binary that lives alongside the currently-running
    interpreter's venv, then ``~/.local/bin/dhee-mcp``, then bare name
    (falls through to PATH at runtime).
    """
    prefix = Path(sys.executable).parent
    candidates = [
        prefix / "dhee-mcp",
        Path.home() / ".local" / "bin" / "dhee-mcp",
        Path.home() / ".dhee" / ".venv" / "bin" / "dhee-mcp",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "dhee-mcp"


@dataclass
class BootstrapResult:
    settings_path: Path
    hooks_installed: bool = False
    mcp_registered: bool = False
    router_enabled: bool = False
    enforce_turned_on: bool = False
    backed_up: Path | None = None
    already_complete: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def bootstrap(
    *,
    enable_router: bool = True,
    register_mcp: bool = True,
    install_hooks: bool = True,
    enforce: bool = True,
) -> BootstrapResult:
    """Run the full Claude Code setup in a single atomic write."""
    path = _settings_path()
    from dhee.hooks.claude_code import install as hook_install
    from dhee.router import install as router_install

    # Snapshot original for rollback + diffing
    original_exists = path.exists()
    if original_exists:
        backup = path.with_suffix(".json.dhee-bootstrap-backup")
        shutil.copy2(path, backup)
        backed_up: Path | None = backup
    else:
        backed_up = None

    # Delegate hook install to existing, tested path. It also does the
    # atomic write itself.
    hooks_result = None
    if install_hooks:
        hooks_result = hook_install.install_hooks()

    # MCP registration + router — a single atomic edit of settings.json
    settings: dict[str, Any] = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            settings = {}

    details: dict[str, Any] = {}
    mcp_changed = False
    if register_mcp:
        mcp = settings.get("mcpServers")
        if not isinstance(mcp, dict):
            mcp = {}
        existing = mcp.get("dhee")
        desired_cmd = _dhee_mcp_binary()
        if not isinstance(existing, dict) or existing.get("command") != desired_cmd:
            env = existing.get("env") if isinstance(existing, dict) else {}
            if not isinstance(env, dict):
                env = {}
            mcp["dhee"] = {
                "command": desired_cmd,
                "args": existing.get("args", []) if isinstance(existing, dict) else [],
                "env": env,
            }
            mcp_changed = True
        settings["mcpServers"] = mcp
        details["mcp_command"] = desired_cmd

    router_changed = False
    if enable_router:
        # ensure dhee MCP server exists first (we just did this above)
        mcp = settings.get("mcpServers") or {}
        dhee_server = mcp.get("dhee") if isinstance(mcp, dict) else None
        if isinstance(dhee_server, dict):
            env = dhee_server.get("env")
            if not isinstance(env, dict):
                env = {}
            if env.get(router_install.ENV_FLAG) != "1":
                env[router_install.ENV_FLAG] = "1"
                router_changed = True
            dhee_server["env"] = env
            mcp["dhee"] = dhee_server
            settings["mcpServers"] = mcp

        perms = settings.get("permissions")
        if not isinstance(perms, dict):
            perms = {}
        allow = perms.get("allow")
        if not isinstance(allow, list):
            allow = []
        added = []
        for tool in router_install.ROUTER_TOOLS:
            if tool not in allow:
                allow.append(tool)
                added.append(tool)
        if added:
            router_changed = True
        perms["allow"] = allow
        perms[router_install.MANAGED_MARKER] = True
        settings["permissions"] = perms
        details["router_allowed"] = list(router_install.ROUTER_TOOLS)

    if mcp_changed or router_changed:
        _atomic_write(path, settings)

    # PreToolUse enforcement: default on. The flag is a filesystem
    # sentinel at ``~/.dhee/router_enforce`` so the hook can check it
    # without loading Dhee's Python. Users opt out by deleting the file
    # or setting ``DHEE_ROUTER_ENFORCE=0``.
    enforce_turned_on = False
    if enforce and enable_router:
        try:
            from dhee.router.pre_tool_gate import _flag_file

            flag = _flag_file()
            flag.parent.mkdir(parents=True, exist_ok=True)
            if not flag.exists():
                flag.write_text("1\n", encoding="utf-8")
                enforce_turned_on = True
        except Exception:
            enforce_turned_on = False

    already_complete = (
        not mcp_changed
        and not router_changed
        and not enforce_turned_on
        and (hooks_result is None or hooks_result.already_installed)
    )

    return BootstrapResult(
        settings_path=path,
        hooks_installed=bool(hooks_result and (hooks_result.created or hooks_result.updated)),
        mcp_registered=mcp_changed,
        router_enabled=router_changed,
        enforce_turned_on=enforce_turned_on,
        backed_up=backed_up,
        already_complete=already_complete,
        details=details,
    )


def teardown() -> BootstrapResult:
    """Reverse of bootstrap: remove hooks + router permissions + MCP flag.

    Leaves the MCP server block in place (users may want to keep Dhee
    tools available without the router). Use ``dhee uninstall`` to fully
    remove Dhee.
    """
    path = _settings_path()
    from dhee.hooks.claude_code import install as hook_install
    from dhee.router import install as router_install

    if not path.exists():
        return BootstrapResult(settings_path=path, already_complete=True)

    backup = path.with_suffix(".json.dhee-bootstrap-backup")
    shutil.copy2(path, backup)

    hook_removed = hook_install.uninstall_hooks()
    router_result = router_install.disable()

    return BootstrapResult(
        settings_path=path,
        hooks_installed=hook_removed,
        router_enabled=router_result.action == "disabled",
        backed_up=backup,
    )
