"""Native harness installation and status management.

This module is the permanent install surface for Dhee-as-product:

* one shared kernel under ``~/.dhee``
* Claude Code wired through native hooks + MCP + router
* Codex wired through native MCP config + global AGENTS override
* CLI config remains the source of truth for on/off state
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from dhee.cli_config import (
    PROVIDER_DEFAULTS,
    load_config,
    save_config,
)

MANAGED_MARKER_START = "<!-- DHEE:START -->"
MANAGED_MARKER_END = "<!-- DHEE:END -->"
CODEX_INSTRUCTIONS_FILE = "AGENTS.override.md"


@dataclass
class HarnessResult:
    harness: str
    action: str
    path: str | None = None
    changed: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def install_harnesses(
    *,
    harness: str = "all",
    enable_router: bool = True,
) -> dict[str, HarnessResult]:
    config = load_config()
    requested = _normalize_harnesses(harness)
    results: dict[str, HarnessResult] = {}
    for name in requested:
        if name == "claude_code":
            results[name] = _install_claude_code(config, enable_router=enable_router)
            config.setdefault("harnesses", {}).setdefault("claude_code", {})["enabled"] = True
            config["harnesses"]["claude_code"]["router"] = bool(enable_router)
        elif name == "codex":
            results[name] = _install_codex(config)
            config.setdefault("harnesses", {}).setdefault("codex", {})["enabled"] = True
        elif name == "gstack":
            results[name] = _install_gstack(config)
            details = results[name].details or {}
            gstack_cfg = config.setdefault("harnesses", {}).setdefault("gstack", {})
            gstack_cfg["enabled"] = results[name].action == "enabled"
            gstack_cfg["path"] = results[name].path
            gstack_cfg["last_ingest_ts"] = details.get("last_ingest_ts")
            gstack_cfg["detected_projects"] = details.get("projects_detected", [])
    save_config(config)
    return results


def disable_harnesses(*, harness: str = "all") -> dict[str, HarnessResult]:
    config = load_config()
    requested = _normalize_harnesses(harness)
    results: dict[str, HarnessResult] = {}
    for name in requested:
        if name == "claude_code":
            results[name] = _disable_claude_code()
            config.setdefault("harnesses", {}).setdefault("claude_code", {})["enabled"] = False
        elif name == "codex":
            results[name] = _disable_codex()
            config.setdefault("harnesses", {}).setdefault("codex", {})["enabled"] = False
        elif name == "gstack":
            results[name] = _disable_gstack()
            config.setdefault("harnesses", {}).setdefault("gstack", {})["enabled"] = False
    save_config(config)
    return results


def harness_status(*, harness: str = "all") -> dict[str, Dict[str, Any]]:
    requested = _normalize_harnesses(harness)
    config = load_config()
    status: dict[str, Dict[str, Any]] = {}
    for name in requested:
        if name == "claude_code":
            status[name] = _status_claude_code(config)
        elif name == "codex":
            status[name] = _status_codex(config)
        elif name == "gstack":
            status[name] = _status_gstack(config)
    return status


def _normalize_harnesses(harness: str) -> list[str]:
    value = str(harness or "all").strip().lower()
    if value == "all":
        return ["claude_code", "codex"]
    if value in {"claude", "claude_code"}:
        return ["claude_code"]
    if value == "codex":
        return ["codex"]
    if value == "gstack":
        return ["gstack"]
    raise ValueError(f"Unsupported harness: {harness}")


def _shared_user_id(config: Dict[str, Any]) -> str:
    return str(((config.get("identity") or {}).get("user_id")) or "default")


def _provider_env(config: Dict[str, Any]) -> Dict[str, str]:
    provider = str(config.get("provider") or "gemini")
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    env: Dict[str, str] = {}
    for key in [defaults.get("env_var"), *(defaults.get("alt_env_vars") or [])]:
        if key and os.environ.get(str(key)):
            env[str(key)] = str(os.environ[str(key)])
    return env


def _dhee_full_mcp_entry() -> str:
    prefix = Path(sys.executable).parent
    candidates = [
        prefix / "dhee-mcp-full",
        Path.home() / ".local" / "bin" / "dhee-mcp-full",
        Path.home() / ".dhee" / ".venv" / "bin" / "dhee-mcp-full",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "dhee-mcp-full"


def _install_claude_code(config: Dict[str, Any], *, enable_router: bool) -> HarnessResult:
    from dhee.router import bootstrap as router_bootstrap

    result = router_bootstrap.bootstrap(
        enable_router=enable_router,
        register_mcp=True,
        install_hooks=True,
        enforce=enable_router,
    )
    settings_path = result.settings_path
    settings = _read_json(settings_path)
    mcp = settings.get("mcpServers") or {}
    server = mcp.get("dhee") if isinstance(mcp, dict) else None
    env = dict(server.get("env") or {}) if isinstance(server, dict) else {}
    env.update(
        {
            "DHEE_HARNESS": "claude_code",
            "DHEE_AGENT_ID": "claude-code",
            "DHEE_SOURCE_APP": "claude_code",
            "DHEE_REQUESTER_AGENT_ID": "claude-code",
            "DHEE_USER_ID": _shared_user_id(config),
        }
    )
    server = {
        "command": _dhee_full_mcp_entry(),
        "args": list(server.get("args", [])) if isinstance(server, dict) else [],
        "env": {**_provider_env(config), **env},
    }
    mcp["dhee"] = server
    settings["mcpServers"] = mcp
    _write_json(settings_path, settings)
    return HarnessResult(
        harness="claude_code",
        action="enabled",
        path=str(settings_path),
        changed=not result.already_complete,
        details={
            "hooks_installed": result.hooks_installed,
            "router_enabled": enable_router,
            "mcp_command": server["command"],
        },
    )


def _disable_claude_code() -> HarnessResult:
    from dhee.router import bootstrap as router_bootstrap

    result = router_bootstrap.teardown()
    settings_path = result.settings_path
    settings = _read_json(settings_path)
    mcp = settings.get("mcpServers")
    changed = False
    if isinstance(mcp, dict) and "dhee" in mcp:
        mcp.pop("dhee", None)
        settings["mcpServers"] = mcp
        _write_json(settings_path, settings)
        changed = True
    return HarnessResult(
        harness="claude_code",
        action="disabled",
        path=str(settings_path),
        changed=changed or result.router_enabled or result.hooks_installed,
    )


def _status_claude_code(config: Dict[str, Any]) -> Dict[str, Any]:
    settings_path = Path.home() / ".claude" / "settings.json"
    settings = _read_json(settings_path)
    hooks = settings.get("hooks") or {}
    mcp = settings.get("mcpServers") or {}
    dhee_server = mcp.get("dhee") if isinstance(mcp, dict) else None
    return {
        "enabled_in_config": bool(((config.get("harnesses") or {}).get("claude_code") or {}).get("enabled", True)),
        "settings_path": str(settings_path),
        "hooks_present": bool(hooks),
        "mcp_registered": isinstance(dhee_server, dict),
        "router_env": ((dhee_server or {}).get("env") or {}).get("DHEE_ROUTER") if isinstance(dhee_server, dict) else None,
    }


def _install_codex(config: Dict[str, Any]) -> HarnessResult:
    config_dir = Path.home() / ".codex"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    sessions_root = config_dir / "sessions"
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    block = _render_codex_mcp_block(config, sessions_root=str(sessions_root))
    updated = _replace_or_append_codex_block(content, block)
    if updated != content:
        config_path.write_text(updated, encoding="utf-8")

    instructions_path = config_dir / CODEX_INSTRUCTIONS_FILE
    _write_managed_markdown_block(instructions_path, _codex_instructions())

    return HarnessResult(
        harness="codex",
        action="enabled",
        path=str(config_path),
        changed=updated != content,
        details={
            "mcp_command": _dhee_full_mcp_entry(),
            "instructions_path": str(instructions_path),
        },
    )


def _disable_codex() -> HarnessResult:
    config_path = Path.home() / ".codex" / "config.toml"
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    updated = _remove_codex_block(content)
    changed = updated != content
    if changed:
        config_path.write_text(updated, encoding="utf-8")

    instructions_path = Path.home() / ".codex" / CODEX_INSTRUCTIONS_FILE
    instructions_changed = _remove_managed_markdown_block(instructions_path)
    return HarnessResult(
        harness="codex",
        action="disabled",
        path=str(config_path),
        changed=changed or instructions_changed,
        details={"instructions_path": str(instructions_path)},
    )


def _status_codex(config: Dict[str, Any]) -> Dict[str, Any]:
    config_path = Path.home() / ".codex" / "config.toml"
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    instructions_path = Path.home() / ".codex" / CODEX_INSTRUCTIONS_FILE
    return {
        "enabled_in_config": bool(((config.get("harnesses") or {}).get("codex") or {}).get("enabled", True)),
        "config_path": str(config_path),
        "mcp_registered": "[mcp_servers.dhee]" in content,
        "instructions_present": instructions_path.exists() and MANAGED_MARKER_START in instructions_path.read_text(encoding="utf-8"),
    }


def _render_codex_mcp_block(config: Dict[str, Any], *, sessions_root: str) -> str:
    env = {
        **_provider_env(config),
        "DHEE_HARNESS": "codex",
        "DHEE_AGENT_ID": "codex",
        "DHEE_SOURCE_APP": "codex",
        "DHEE_REQUESTER_AGENT_ID": "codex",
        "DHEE_USER_ID": _shared_user_id(config),
        "DHEE_CODEX_AUTO_SYNC": "1",
        "DHEE_CODEX_SESSIONS_ROOT": sessions_root,
    }
    lines = [
        '[mcp_servers.dhee]',
        f'command = "{_escape_toml(_dhee_full_mcp_entry())}"',
        'args = []',
        '',
        '[mcp_servers.dhee.env]',
    ]
    for key, value in sorted(env.items()):
        lines.append(f'{key} = "{_escape_toml(value)}"')
    return "\n".join(lines).rstrip() + "\n"


def _replace_or_append_codex_block(content: str, block: str) -> str:
    pattern = re.compile(
        r"(?ms)^\[mcp_servers\.dhee\]\n.*?(?=^\[(?!mcp_servers\.dhee(?:\.|\]))|\Z)"
    )
    if pattern.search(content):
        return pattern.sub(block.rstrip() + "\n\n", content, count=1).rstrip() + "\n"
    trimmed = content.rstrip()
    if trimmed:
        trimmed += "\n\n"
    return trimmed + block


def _remove_codex_block(content: str) -> str:
    pattern = re.compile(
        r"(?ms)^\[mcp_servers\.dhee\]\n.*?(?=^\[(?!mcp_servers\.dhee(?:\.|\]))|\Z)"
    )
    updated = pattern.sub("", content)
    return updated.rstrip() + ("\n" if updated.strip() else "")


def _codex_instructions() -> str:
    return (
        "# Dhee Native Integration\n\n"
        "Dhee is the primary memory and context-router for this Codex session.\n\n"
        "Required behavior:\n"
        "- At task start in a repo, call `dhee_handoff` to recover compact repo/task continuity.\n"
        "- If there is an active shared repo task, call `dhee_shared_task_results` before repeating work.\n"
        "- Prefer `dhee_read`, `dhee_grep`, and `dhee_bash` for reads/searches/commands that may create large reusable output.\n"
        "- After completing a substantial native tool action, refresh Dhee collaboration context before continuing on shared work.\n"
        "- Treat Dhee memories, artifacts, and shared-task results as the canonical reusable context for this repo.\n"
    )


def _write_managed_markdown_block(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    block = f"{MANAGED_MARKER_START}\n{body.rstrip()}\n{MANAGED_MARKER_END}\n"
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(?s){re.escape(MANAGED_MARKER_START)}.*?{re.escape(MANAGED_MARKER_END)}\n?"
    )
    if pattern.search(content):
        path.write_text(pattern.sub(block, content), encoding="utf-8")
    else:
        suffix = "" if not content.strip() else "\n\n"
        path.write_text(content.rstrip() + suffix + block, encoding="utf-8")


def _remove_managed_markdown_block(path: Path) -> bool:
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(?s){re.escape(MANAGED_MARKER_START)}.*?{re.escape(MANAGED_MARKER_END)}\n?"
    )
    updated = pattern.sub("", content).rstrip()
    if updated:
        path.write_text(updated + "\n", encoding="utf-8")
    else:
        path.unlink()
    return updated != content.rstrip()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _escape_toml(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# gstack adapter
# ---------------------------------------------------------------------------


def _install_gstack(config: Dict[str, Any]) -> HarnessResult:
    from dhee.adapters import gstack as gstack_adapter

    detected = gstack_adapter.detect()
    if not detected.installed and not detected.projects:
        return HarnessResult(
            harness="gstack",
            action="skipped",
            path=detected.gstack_home,
            changed=False,
            details={
                "reason": "gstack_not_detected",
                "looked_for": str(Path.home() / ".claude" / "skills" / "gstack" / "VERSION"),
                "gstack_home": detected.gstack_home,
            },
        )
    report = gstack_adapter.backfill()
    return HarnessResult(
        harness="gstack",
        action="enabled",
        path=detected.gstack_home,
        changed=report.get("atoms_total", 0) > 0,
        details={
            "projects_detected": detected.projects,
            "atoms_ingested": report.get("atoms_total", 0),
            "last_ingest_ts": report.get("last_ingest_ts"),
            "gstack_version": detected.version,
        },
    )


def _disable_gstack() -> HarnessResult:
    from dhee.adapters import gstack as gstack_adapter

    cleared = gstack_adapter.clear_manifest()
    return HarnessResult(
        harness="gstack",
        action="disabled",
        path=str(Path.home() / ".dhee" / "gstack_manifest.json"),
        changed=cleared,
        details={"manifest_cleared": cleared},
    )


def _status_gstack(config: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.adapters import gstack as gstack_adapter

    info = gstack_adapter.status()
    enabled = bool(((config.get("harnesses") or {}).get("gstack") or {}).get("enabled", False))
    return {
        "enabled_in_config": enabled,
        "installed": info["detected"]["installed"],
        "gstack_home": info["detected"]["gstack_home"],
        "projects_detected": info["detected"]["projects"],
        "projects_tracked": info["projects_tracked"],
        "manifest_path": info["manifest_path"],
        "last_ingest_ts": info["last_ingest_ts"],
        "gstack_version": info["detected"]["version"],
    }
