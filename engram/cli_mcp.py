"""Auto-detect and configure MCP servers for AI agents."""

import json
import os
import sys
from typing import Any, Dict, List, Tuple

from engram.cli_config import PROVIDER_DEFAULTS


def _engram_mcp_entry() -> str:
    """Return the engram-mcp command path."""
    # Prefer the entry point in the same prefix as the running Python
    prefix = os.path.dirname(os.path.dirname(sys.executable))
    candidates = [
        os.path.join(prefix, "bin", "engram-mcp"),
        os.path.join(os.path.expanduser("~"), ".local", "bin", "engram-mcp"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "engram-mcp"


def _build_env_block(config: Dict[str, Any]) -> Dict[str, str]:
    """Build env vars to inject into MCP config."""
    provider = config.get("provider", "gemini")
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    env_var = defaults.get("env_var")
    env = {}
    if env_var:
        key = os.environ.get(env_var, "")
        for alt in defaults.get("alt_env_vars", []):
            key = key or os.environ.get(alt, "")
        if key:
            env[env_var] = key
    return env


def _mcp_server_block(config: Dict[str, Any]) -> Dict[str, Any]:
    """Build the MCP server config block for engram."""
    return {
        "command": _engram_mcp_entry(),
        "args": [],
        "env": _build_env_block(config),
    }


def _read_json(path: str) -> Dict[str, Any]:
    """Read a JSON file, return empty dict if missing or invalid."""
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _write_json(path: str, data: Dict[str, Any]) -> None:
    """Write JSON file, creating parent dirs."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _read_toml_mcp_servers(path: str) -> Dict[str, Any]:
    """Read MCP servers from a TOML config file (for Codex)."""
    if not os.path.exists(path):
        return {}
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("mcp_servers", data.get("mcpServers", {}))


def _configure_claude_code(config: Dict[str, Any]) -> str:
    """Configure Claude Code (~/.claude.json)."""
    path = os.path.join(os.path.expanduser("~"), ".claude.json")
    data = _read_json(path)
    if "mcpServers" not in data:
        data["mcpServers"] = {}
    data["mcpServers"]["engram"] = _mcp_server_block(config)
    _write_json(path, data)
    return "configured"


def _configure_claude_desktop(config: Dict[str, Any]) -> str:
    """Configure Claude Desktop."""
    if sys.platform == "darwin":
        path = os.path.join(
            os.path.expanduser("~"),
            "Library", "Application Support", "Claude", "claude_desktop_config.json",
        )
    else:
        path = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "Claude", "claude_desktop_config.json",
        )
    if not os.path.exists(os.path.dirname(path)):
        return "not installed"
    data = _read_json(path)
    if "mcpServers" not in data:
        data["mcpServers"] = {}
    data["mcpServers"]["engram"] = _mcp_server_block(config)
    _write_json(path, data)
    return "configured"


def _configure_cursor(config: Dict[str, Any]) -> str:
    """Configure Cursor (~/.cursor/mcp.json)."""
    path = os.path.join(os.path.expanduser("~"), ".cursor", "mcp.json")
    if not os.path.exists(os.path.dirname(path)):
        return "not installed"
    data = _read_json(path)
    if "mcpServers" not in data:
        data["mcpServers"] = {}
    data["mcpServers"]["engram"] = _mcp_server_block(config)
    _write_json(path, data)
    return "configured"


def _configure_codex(config: Dict[str, Any]) -> str:
    """Configure Codex (~/.codex/config.toml) — append MCP server."""
    config_dir = os.path.join(os.path.expanduser("~"), ".codex")
    toml_path = os.path.join(config_dir, "config.toml")
    if not os.path.exists(config_dir):
        return "not installed"
    # Codex uses TOML — we append a simple section if not present
    content = ""
    if os.path.exists(toml_path):
        with open(toml_path, "r") as f:
            content = f.read()
    if "engram" in content:
        return "already configured"
    env = _build_env_block(config)
    env_lines = "\n".join(f'  {k} = "{v}"' for k, v in env.items())
    block = (
        f'\n[mcp_servers.engram]\n'
        f'command = "{_engram_mcp_entry()}"\n'
        f'args = []\n'
    )
    if env_lines:
        block += f'[mcp_servers.engram.env]\n{env_lines}\n'
    with open(toml_path, "a") as f:
        f.write(block)
    return "configured"


# Agent registry: (name, detector, configurer)
_AGENTS: List[Tuple[str, str, Any]] = [
    ("Claude Code", "~/.claude.json", _configure_claude_code),
    ("Claude Desktop", None, _configure_claude_desktop),
    ("Cursor", "~/.cursor", _configure_cursor),
    ("Codex", "~/.codex", _configure_codex),
]


def detect_agents() -> List[str]:
    """Return list of detected agent names."""
    found = []
    for name, marker, _ in _AGENTS:
        if marker is None:
            # Special detection logic (Claude Desktop)
            if sys.platform == "darwin":
                path = os.path.join(
                    os.path.expanduser("~"),
                    "Library", "Application Support", "Claude",
                )
            else:
                path = os.path.join(
                    os.environ.get("APPDATA", os.path.expanduser("~")), "Claude",
                )
            if os.path.exists(path):
                found.append(name)
        else:
            if os.path.exists(os.path.expanduser(marker)):
                found.append(name)
    return found


def configure_mcp_servers(config: Dict[str, Any]) -> Dict[str, str]:
    """Auto-detect agents and configure MCP. Returns {agent: status}."""
    results = {}
    for name, marker, configure_fn in _AGENTS:
        try:
            status = configure_fn(config)
            if status != "not installed":
                results[name] = status
        except Exception as e:
            results[name] = f"error: {e}"
    return results
