"""Native harness installation and status management.

This module is the permanent install surface for Dhee-as-product:

* one shared kernel under ``~/.dhee``
* Claude Code wired through native hooks + MCP + router
* Codex wired through native MCP config + global AGENTS.md instructions
* CLI config remains the source of truth for on/off state
* Hermes is auto-detected and wired as Dhee's native memory provider when present
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from dhee.cli_config import (
    PROVIDER_DEFAULTS,
    load_config,
    save_config,
)
from dhee.provider_defaults import DEFAULT_PROVIDER

MANAGED_MARKER_START = "<!-- DHEE:START -->"
MANAGED_MARKER_END = "<!-- DHEE:END -->"
CODEX_INSTRUCTIONS_FILE = "AGENTS.md"
LEGACY_CODEX_INSTRUCTIONS_FILE = "AGENTS.override.md"
CODEX_NATIVE_LEVEL = "closest_available"
CODEX_NATIVE_SURFACES = (
    "codex_mcp_config",
    "codex_global_agents_md",
    "mcp_server_instructions",
    "codex_session_stream_auto_sync",
)
CODEX_CONTEXT_FIRST_TOOLS = (
    "dhee_context_bootstrap",
    "dhee_inbox",
    "dhee_search_learnings",
)
CODEX_ROUTER_TOOLS = (
    "dhee_read",
    "dhee_grep",
    "dhee_bash",
    "dhee_expand_result",
)
CODEX_TRUSTED_READ_ONLY_TOOLS = (
    "recall",
    "context",
    "dhee_context_bootstrap",
    "dhee_handoff",
    "dhee_shared_task_results",
    "dhee_inbox",
    "dhee_search_learnings",
    "dhee_context_status",
    "dhee_context_state",
    "dhee_scene_search",
    "dhee_context_pack",
    "dhee_task_contract_compile",
    "dhee_task_contract_list",
    "dhee_task_contract_get",
    "dhee_task_contract_interpret",
    "dhee_contract_runtime_status",
    "dhee_contract_enforcement_status",
    "dhee_contract_runtime_doctor",
    "dhee_update_capsule_list",
    "dhee_update_capsule_get",
    "dhee_update_capsule_interpret",
    "dhee_tools_list",
    "dhee_list_assets",
    "dhee_get_asset",
    "dhee_why",
    "dhee_read",
    "dhee_grep",
    "dhee_agent",
    "dhee_expand_result",
)


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
        elif name == "cursor":
            results[name] = _install_cursor(config)
            cur_cfg = config.setdefault("harnesses", {}).setdefault("cursor", {})
            cur_cfg["enabled"] = True
            cur_cfg["rule_path"] = results[name].path
        elif name == "hermes":
            results[name] = _install_hermes(config)
            hermes_cfg = config.setdefault("harnesses", {}).setdefault("hermes", {})
            hermes_cfg["enabled"] = results[name].action == "enabled"
            hermes_cfg["path"] = results[name].path
            hermes_cfg.update(results[name].details or {})
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
        elif name == "cursor":
            results[name] = _disable_cursor()
            config.setdefault("harnesses", {}).setdefault("cursor", {})["enabled"] = False
        elif name == "hermes":
            results[name] = _disable_hermes()
            config.setdefault("harnesses", {}).setdefault("hermes", {})["enabled"] = False
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
        elif name == "cursor":
            status[name] = _status_cursor(config)
        elif name == "hermes":
            status[name] = _status_hermes(config)
    return status


def _normalize_harnesses(harness: str) -> list[str]:
    value = str(harness or "all").strip().lower()
    if value == "all":
        return ["claude_code", "codex", "hermes"]
    if value in {"claude", "claude_code"}:
        return ["claude_code"]
    if value == "codex":
        return ["codex"]
    if value == "gstack":
        return ["gstack"]
    if value == "cursor":
        return ["cursor"]
    if value == "hermes":
        return ["hermes"]
    raise ValueError(f"Unsupported harness: {harness}")


def _shared_user_id(config: Dict[str, Any]) -> str:
    return str(((config.get("identity") or {}).get("user_id")) or "default")


def _provider_env(config: Dict[str, Any]) -> Dict[str, str]:
    provider = str(config.get("provider") or DEFAULT_PROVIDER)
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
    existing = [candidate for candidate in candidates if candidate.exists()]
    healthy = [candidate for candidate in existing if _mcp_entry_is_healthy(candidate)]
    if healthy:
        return str(healthy[0])
    if existing:
        return str(existing[0])
    return "dhee-mcp-full"


def _mcp_entry_is_healthy(entry: Path) -> bool:
    """Best-effort smoke check for the Python behind an MCP entrypoint."""

    try:
        first = entry.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return False
    if not first.startswith("#!"):
        return False
    python = first[2:].strip()
    if not python:
        return False
    probe = (
        "import dhee.mcp_server\n"
        "try:\n"
        "    import sqlite_vec\n"
        "except ModuleNotFoundError:\n"
        "    raise SystemExit(7)\n"
    )
    try:
        result = subprocess.run(
            [python, "-c", probe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


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
            "DHEE_AUTO_CONTINUITY": "1",
            "DHEE_SHARED_CONTEXT_FIRST": "1",
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
        "auto_continuity": ((dhee_server or {}).get("env") or {}).get("DHEE_AUTO_CONTINUITY") if isinstance(dhee_server, dict) else None,
        "shared_context_first": ((dhee_server or {}).get("env") or {}).get("DHEE_SHARED_CONTEXT_FIRST") if isinstance(dhee_server, dict) else None,
    }


def _install_codex(config: Dict[str, Any]) -> HarnessResult:
    config_dir = Path.home() / ".codex"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    sessions_root = config_dir / "sessions"
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    block = _render_codex_mcp_block(config, sessions_root=str(sessions_root))
    updated = _replace_or_append_codex_block(content, block)
    backup_path = _backup_file(config_path, "dhee-codex") if updated != content and config_path.exists() else None
    if updated != content:
        config_path.write_text(updated, encoding="utf-8")

    instructions_path = config_dir / CODEX_INSTRUCTIONS_FILE
    instructions_changed = _write_managed_markdown_block(instructions_path, _codex_instructions())
    legacy_instructions_changed = _remove_managed_markdown_block(config_dir / LEGACY_CODEX_INSTRUCTIONS_FILE)

    return HarnessResult(
        harness="codex",
        action="enabled",
        path=str(config_path),
        changed=updated != content or instructions_changed or legacy_instructions_changed,
        details={
            "mcp_command": _dhee_full_mcp_entry(),
            "instructions_path": str(instructions_path),
            "legacy_instructions_removed": legacy_instructions_changed,
            "backup": str(backup_path) if backup_path else None,
            "native": True,
            "native_level": CODEX_NATIVE_LEVEL,
            "native_surfaces": list(CODEX_NATIVE_SURFACES),
            "context_first_tools": list(CODEX_CONTEXT_FIRST_TOOLS),
            "router_tools": list(CODEX_ROUTER_TOOLS),
            "auto_sync": True,
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
    legacy_instructions_changed = _remove_managed_markdown_block(
        Path.home() / ".codex" / LEGACY_CODEX_INSTRUCTIONS_FILE
    )
    return HarnessResult(
        harness="codex",
        action="disabled",
        path=str(config_path),
        changed=changed or instructions_changed or legacy_instructions_changed,
        details={
            "instructions_path": str(instructions_path),
            "legacy_instructions_path": str(Path.home() / ".codex" / LEGACY_CODEX_INSTRUCTIONS_FILE),
        },
    )


def _status_codex(config: Dict[str, Any]) -> Dict[str, Any]:
    config_path = Path.home() / ".codex" / "config.toml"
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    instructions_path = Path.home() / ".codex" / CODEX_INSTRUCTIONS_FILE
    legacy_instructions_path = Path.home() / ".codex" / LEGACY_CODEX_INSTRUCTIONS_FILE
    dhee_block = _codex_mcp_block(content)
    mcp_registered = bool(dhee_block)
    return {
        "enabled_in_config": bool(((config.get("harnesses") or {}).get("codex") or {}).get("enabled", True)),
        "config_path": str(config_path),
        "mcp_registered": mcp_registered,
        "native": _codex_native_enabled(dhee_block, instructions_path) if mcp_registered else False,
        "native_level": _codex_env_value(dhee_block, "DHEE_CODEX_NATIVE_LEVEL") if mcp_registered else None,
        "native_surfaces": _split_codex_env_list(
            _codex_env_value(dhee_block, "DHEE_CODEX_NATIVE_SURFACES")
        ) if mcp_registered else [],
        "router_env": _codex_env_value(dhee_block, "DHEE_ROUTER") if mcp_registered else None,
        "router_contract": _codex_env_value(dhee_block, "DHEE_CODEX_ROUTER_CONTRACT") if mcp_registered else None,
        "context_first": _codex_env_value(dhee_block, "DHEE_CONTEXT_FIRST") if mcp_registered else None,
        "shared_context_first": _codex_env_value(dhee_block, "DHEE_SHARED_CONTEXT_FIRST") if mcp_registered else None,
        "auto_sync": _codex_env_value(dhee_block, "DHEE_CODEX_AUTO_SYNC") if mcp_registered else None,
        "context_first_tools": _codex_env_value(dhee_block, "DHEE_CONTEXT_FIRST_TOOLS") if mcp_registered else None,
        "router_tools": _codex_env_value(dhee_block, "DHEE_ROUTER_TOOLS") if mcp_registered else None,
        "trusted_read_only_tools": _codex_trusted_read_only_tools(dhee_block) if mcp_registered else [],
        "instructions_present": instructions_path.exists() and MANAGED_MARKER_START in instructions_path.read_text(encoding="utf-8"),
        "instructions_path": str(instructions_path),
        "legacy_instructions_present": legacy_instructions_path.exists()
        and MANAGED_MARKER_START in legacy_instructions_path.read_text(encoding="utf-8"),
    }


def _render_codex_mcp_block(config: Dict[str, Any], *, sessions_root: str) -> str:
    env = {
        **_provider_env(config),
        "DHEE_HARNESS": "codex",
        "DHEE_AGENT_ID": "codex",
        "DHEE_SOURCE_APP": "codex",
        "DHEE_REQUESTER_AGENT_ID": "codex",
        "DHEE_USER_ID": _shared_user_id(config),
        "DHEE_AUTO_CONTINUITY": "1",
        "DHEE_CODEX_NATIVE": "1",
        "DHEE_CODEX_NATIVE_LEVEL": CODEX_NATIVE_LEVEL,
        "DHEE_CODEX_NATIVE_SURFACES": ",".join(CODEX_NATIVE_SURFACES),
        "DHEE_CODEX_ROUTER_CONTRACT": "context_first",
        "DHEE_CODEX_AUTO_SYNC": "1",
        "DHEE_CODEX_SESSIONS_ROOT": sessions_root,
        "DHEE_CONTEXT_FIRST_TOOLS": ",".join(CODEX_CONTEXT_FIRST_TOOLS),
        "DHEE_CONTEXT_FIRST": "1",
        "DHEE_ROUTER": "1",
        "DHEE_ROUTER_TOOLS": ",".join(CODEX_ROUTER_TOOLS),
        "DHEE_SHARED_CONTEXT_FIRST": "1",
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
    for tool_name in CODEX_TRUSTED_READ_ONLY_TOOLS:
        lines.extend(
            [
                "",
                f"[mcp_servers.dhee.tools.{tool_name}]",
                'approval_mode = "auto"',
            ]
        )
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


def _cursor_rule_body() -> str:
    return (
        "Dhee is the primary memory and context-router for this repository. "
        "Cursor will inject this rule into every conversation automatically.\n\n"
        "Required behavior:\n"
        "- When a knowledge graph or `.dhee/config.json` exists, navigate by "
        "structure first — check god nodes and community summaries before "
        "grepping raw files.\n"
        "- Prefer `dhee_read`, `dhee_grep`, and `dhee_bash` (when available "
        "via MCP) for reads/searches/commands that produce large reusable "
        "output.\n"
        "- Check `dhee_inbox` when working on shared context, and use "
        "`dhee_broadcast` for updates another active agent must see now.\n"
        "- Treat Dhee memories, AST extractions, and team context as the "
        "canonical reusable context for this repo.\n"
        "- For long files (>20 KB), request a digest before reading raw "
        "contents end-to-end.\n"
    )


def _install_cursor(config: Dict[str, Any], *, project_root: Path | None = None) -> HarnessResult:
    """Cursor installs a project-local always-applied rule.

    No hooks needed — Cursor injects ``.cursor/rules/*.mdc`` files with
    ``alwaysApply: true`` into every conversation. We write
    ``.cursor/rules/dhee.mdc`` at the repo root (or ``project_root`` if
    given). Idempotent.
    """
    root = (project_root or Path.cwd()).resolve()
    rules_dir = root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_path = rules_dir / "dhee.mdc"

    body = (
        "---\n"
        "description: Dhee — context-router and memory layer\n"
        "alwaysApply: true\n"
        "---\n\n"
        + _cursor_rule_body()
    )
    changed = True
    if rule_path.exists():
        try:
            changed = rule_path.read_text(encoding="utf-8") != body
        except OSError:
            changed = True
    if changed:
        rule_path.write_text(body, encoding="utf-8")

    return HarnessResult(
        harness="cursor",
        action="enabled",
        path=str(rule_path),
        changed=changed,
        details={
            "project_root": str(root),
            "always_apply": True,
        },
    )


def _disable_cursor(*, project_root: Path | None = None) -> HarnessResult:
    root = (project_root or Path.cwd()).resolve()
    rule_path = root / ".cursor" / "rules" / "dhee.mdc"
    changed = False
    if rule_path.exists():
        rule_path.unlink()
        changed = True
    return HarnessResult(
        harness="cursor",
        action="disabled",
        path=str(rule_path),
        changed=changed,
        details={"project_root": str(root)},
    )


def _status_cursor(config: Dict[str, Any], *, project_root: Path | None = None) -> Dict[str, Any]:
    root = (project_root or Path.cwd()).resolve()
    rule_path = root / ".cursor" / "rules" / "dhee.mdc"
    return {
        "enabled_in_config": bool(((config.get("harnesses") or {}).get("cursor") or {}).get("enabled", False)),
        "rule_path": str(rule_path),
        "rule_present": rule_path.exists(),
        "project_root": str(root),
    }


def _install_hermes(config: Dict[str, Any]) -> HarnessResult:
    from dhee.integrations import hermes as hermes_integration

    detected = hermes_integration.detect_hermes()
    if not detected.get("installed"):
        return HarnessResult(
            harness="hermes",
            action="skipped",
            path=detected.get("hermes_home"),
            changed=False,
            details={
                "reason": "hermes_not_detected",
                "binary": detected.get("binary"),
                "looked_for": detected.get("hermes_home"),
            },
        )

    result = hermes_integration.install_provider(
        hermes_home_path=detected.get("hermes_home"),
        enable=True,
        dhee_data_dir=os.environ.get("DHEE_DATA_DIR"),
        sync_existing=True,
        promote_imported=True,
    )
    sync = result.get("sync") or {}
    return HarnessResult(
        harness="hermes",
        action="enabled",
        path=result.get("plugin_dir"),
        changed=bool(result.get("changed")),
        details={
            "hermes_home": result.get("hermes_home"),
            "plugin_dir": result.get("plugin_dir"),
            "active_provider": "dhee",
            "backup": result.get("backup"),
            "imported_learnings": sync.get("imported_count", 0),
            "promoted_learnings": sync.get("promoted_count", 0),
            "candidate_learnings": sync.get("candidate_count", 0),
            "policy_updates": sync.get("updated_policy_count", 0),
            "skipped_learnings": sync.get("skipped_count", 0),
            "promoted_import": bool(sync.get("promote", True)) if sync else True,
            "detected_sessions": detected.get("session_count", 0),
            "detected_agent_skills": detected.get("agent_skill_count", 0),
        },
    )


def _disable_hermes() -> HarnessResult:
    from dhee.integrations import hermes as hermes_integration

    result = hermes_integration.disable_provider()
    return HarnessResult(
        harness="hermes",
        action="disabled",
        path=result.get("hermes_config"),
        changed=bool(result.get("disabled")),
        details={
            "hermes_home": result.get("hermes_home"),
            "backup": result.get("backup"),
        },
    )


def _status_hermes(config: Dict[str, Any]) -> Dict[str, Any]:
    from dhee.integrations import hermes as hermes_integration

    detected = hermes_integration.detect_hermes()
    provider = hermes_integration.provider_status(detected.get("hermes_home"))
    enabled_cfg = bool(((config.get("harnesses") or {}).get("hermes") or {}).get("enabled", False))
    return {
        "enabled_in_config": enabled_cfg,
        "installed": detected.get("installed", False),
        "binary": detected.get("binary"),
        "hermes_home": detected.get("hermes_home"),
        "config_path": detected.get("config_path"),
        "active_provider": provider.get("active_provider"),
        "plugin_installed": provider.get("plugin_installed", False),
        "mcp_registered": provider.get("enabled", False),
        "learning_store": provider.get("learning_store"),
        "last_sync": provider.get("last_sync"),
        "session_count": detected.get("session_count", 0),
        "agent_skill_count": detected.get("agent_skill_count", 0),
    }


def _codex_instructions() -> str:
    return (
        "# Dhee Native Integration\n\n"
        "Dhee is the primary memory, context-router, and shared continuity layer for this Codex session.\n\n"
        "Codex-native surfaces:\n"
        "- Dhee is registered in `~/.codex/config.toml` as the Codex MCP server.\n"
        "- This global `~/.codex/AGENTS.md` block is loaded by Codex on every session.\n"
        "- The Dhee MCP server advertises context-first instructions during MCP initialization.\n"
        "- Dhee syncs Codex session logs opportunistically on Dhee context and collaboration calls.\n\n"
        "Required behavior:\n"
        "- Start every substantive repo/workspace task with Dhee's router path before local reconstruction.\n"
        "- First call `dhee_context_bootstrap` with the absolute repo path before shell/file exploration.\n"
        "- Treat `dhee_context_bootstrap` as the normal one-call replacement for startup `dhee_handoff`, `dhee_shared_task`, `dhee_shared_task_results`, and `dhee_inbox` checks.\n"
        "- Fall back to the separate startup tools only when bootstrap is unavailable or you need a specific legacy call.\n"
        "- When the user says continue, resume, previous, shared context, or UI context, treat Dhee handoff/shared-task results as the source of continuity.\n"
        "- Prefer `dhee_read`, `dhee_grep`, and `dhee_bash` over native file/search/shell tools for exploration that may create large reusable output.\n"
        "- Use `dhee_expand_result` only when a digest pointer needs exact raw details.\n"
        "- Search promoted learnings with `dhee_search_learnings` when prior Dhee/Hermes self-evolution may apply.\n"
        "- After completing a substantial native tool action, refresh Dhee collaboration context with `dhee_inbox` before continuing on shared work.\n"
        "- When you discover context another active agent needs now, call `dhee_broadcast` instead of waiting for session end.\n"
        "- Treat Dhee memories, artifacts, repo-shared context, and shared-task results as the canonical reusable context for this repo.\n"
    )


def _codex_env_value(content: str, key: str) -> str | None:
    match = re.search(rf'(?m)^\s*{re.escape(key)}\s*=\s*"((?:\\.|[^"\\])*)"\s*$', content)
    if not match:
        return None
    return match.group(1).replace('\\"', '"').replace("\\\\", "\\")


def _codex_trusted_read_only_tools(content: str) -> list[str]:
    pattern = re.compile(
        r'(?ms)^\[mcp_servers\.dhee\.tools\.([^\]\s]+)\]\s*\n'
        r'(?:(?!^\[).)*?^\s*approval_mode\s*=\s*"(?:auto|never)"\s*$'
    )
    return sorted({match.group(1) for match in pattern.finditer(content or "")})


def _codex_mcp_block(content: str) -> str:
    match = re.search(
        r"(?ms)^\[mcp_servers\.dhee\]\n.*?(?=^\[(?!mcp_servers\.dhee(?:\.|\]))|\Z)",
        content,
    )
    return match.group(0) if match else ""


def _split_codex_env_list(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _codex_native_enabled(dhee_block: str, instructions_path: Path) -> bool:
    instructions_present = (
        instructions_path.exists()
        and MANAGED_MARKER_START in instructions_path.read_text(encoding="utf-8")
    )
    return (
        instructions_present
        and _codex_env_value(dhee_block, "DHEE_CODEX_NATIVE") == "1"
        and _codex_env_value(dhee_block, "DHEE_CONTEXT_FIRST") == "1"
        and _codex_env_value(dhee_block, "DHEE_ROUTER") == "1"
    )


def _backup_file(path: Path, tag: str) -> Path:
    backup = path.with_suffix(path.suffix + f".{tag}-backup")
    shutil.copy2(path, backup)
    return backup


def _write_managed_markdown_block(path: Path, body: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    block = f"{MANAGED_MARKER_START}\n{body.rstrip()}\n{MANAGED_MARKER_END}\n"
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return True
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(?s){re.escape(MANAGED_MARKER_START)}.*?{re.escape(MANAGED_MARKER_END)}\n?"
    )
    if pattern.search(content):
        updated = pattern.sub(block, content)
    else:
        suffix = "" if not content.strip() else "\n\n"
        updated = content.rstrip() + suffix + block
    if updated != content:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


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
