"""Engram CLI tools for installation and configuration.

Commands:
    engram-install: Install Engram MCP server into Claude Code, Claude Desktop, Cursor, and Codex
"""

import json
import os
import sys
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List

# TOML support (built-in since Python 3.11, fallback for earlier versions)
try:
    import tomllib  # Python 3.11+
    HAS_TOMLLIB = True
except ImportError:
    HAS_TOMLLIB = False

try:
    import tomli_w  # For writing TOML
    HAS_TOMLI_W = True
except ImportError:
    HAS_TOMLI_W = False

_CONTINUITY_START = "<!-- ENGRAM_CONTINUITY:START -->"
_CONTINUITY_END = "<!-- ENGRAM_CONTINUITY:END -->"


def _handoff_block(agent_id: str, agent_label: str) -> str:
    return f"""\
{_CONTINUITY_START}
## Engram Continuity (Auto-Generated)

Follow these rules for cross-agent continuity on every new task/thread.

1) Before answering substantive repo/task questions, call `get_last_session`:
- `user_id`: `"default"` unless provided
- `requester_agent_id`: `"{agent_id}"`
- `repo`: absolute workspace path
- Include `agent_id` only when the user explicitly asks to continue from a specific source agent.

2) If no handoff session exists, continue normally and use memory tools as needed.

3) On major milestones and before pausing/ending, call `save_session_digest` with:
- `task_summary`
- `repo`
- `status` (`"active"`, `"paused"`, or `"completed"`)
- `decisions_made`, `files_touched`, `todos_remaining`
- `blockers`, `key_commands`, `test_results` when available
- `agent_id`: `"{agent_id}"`, `requester_agent_id`: `"{agent_id}"`

4) Prefer Engram MCP handoff tools over shell/SQLite inspection for continuity.

Target agent profile: `{agent_label}`.
{_CONTINUITY_END}
"""


def _cursor_rule_content() -> str:
    return """\
---
description: "Use Engram handoff tools automatically for continuity"
alwaysApply: true
---

When an Engram MCP server is available:

1) At the start of a new task/thread, call `get_last_session` first.
   Do not pass `agent_id` unless the user explicitly asks for a specific source agent.
2) Use the returned handoff context to continue work naturally.
3) Before pausing or ending, call `save_session_digest`.
4) Do not use shell/SQLite probing for continuity when MCP handoff tools exist.
"""


def _upsert_block_file(path: Path, block: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    start_idx = existing.find(_CONTINUITY_START)
    end_idx = existing.find(_CONTINUITY_END)

    if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
        end_idx += len(_CONTINUITY_END)
        updated = existing[:start_idx] + block + existing[end_idx:]
    elif existing.strip():
        updated = existing.rstrip() + "\n\n" + block + "\n"
    else:
        updated = block + "\n"

    if updated == existing:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return True


def _write_file_if_changed(path: Path, content: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    normalized = content if content.endswith("\n") else content + "\n"
    if existing == normalized:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")
    return True


def _install_workspace_continuity_rules(workspace: Path) -> List[str]:
    updated: List[str] = []

    targets = [
        (workspace / "AGENTS.md", _handoff_block("codex", "Codex/agent-runner")),
        (workspace / "CLAUDE.md", _handoff_block("claude-code", "Claude Code")),
        (workspace / "CURSOR.md", _handoff_block("cursor", "Cursor")),
    ]
    for path, block in targets:
        if _upsert_block_file(path, block):
            updated.append(str(path))

    cursor_rule_path = workspace / ".cursor" / "rules" / "engram-continuity.mdc"
    if _write_file_if_changed(cursor_rule_path, _cursor_rule_content()):
        updated.append(str(cursor_rule_path))

    return updated


def _config_with_agent_identity(server_config: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
    updated = dict(server_config)
    env = dict(updated.get("env", {}))
    env["ENGRAM_MCP_AGENT_ID"] = agent_id
    updated["env"] = env
    return updated


def _read_toml(path: Path) -> Dict[str, Any]:
    """Read a TOML file."""
    if not path.exists():
        return {}

    with open(path, 'rb') as f:
        if HAS_TOMLLIB:
            import tomllib
            return tomllib.load(f)
        else:
            # Fallback: simple TOML parsing for basic cases
            # This won't handle all TOML features but works for MCP config
            return _simple_toml_parse(path)


def _simple_toml_parse(path: Path) -> Dict[str, Any]:
    """Simple TOML parser for basic MCP config (fallback when tomllib unavailable)."""
    result: Dict[str, Any] = {}
    current_section = None

    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Section header
            if line.startswith('[') and line.endswith(']'):
                section = line[1:-1]
                parts = section.split('.')
                current_section = result
                for part in parts:
                    if part not in current_section:
                        current_section[part] = {}
                    current_section = current_section[part]
            # Key-value pair
            elif '=' in line and current_section is not None:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                # Parse value
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith('[') and value.endswith(']'):
                    # Simple array parsing
                    value = [v.strip().strip('"') for v in value[1:-1].split(',') if v.strip()]
                current_section[key] = value

    return result


def _write_toml(path: Path, data: Dict[str, Any]) -> None:
    """Write a TOML file."""
    if HAS_TOMLI_W:
        import tomli_w
        with open(path, 'wb') as f:
            tomli_w.dump(data, f)
    else:
        # Fallback: write simple TOML manually
        with open(path, 'w') as f:
            _write_toml_section(f, data, [])


def _write_toml_section(f, data: Dict[str, Any], path: list) -> None:
    """Write a TOML section recursively."""
    # First write simple key-value pairs at this level
    for key, value in data.items():
        if not isinstance(value, dict):
            if isinstance(value, str):
                f.write(f'{key} = "{value}"\n')
            elif isinstance(value, list):
                items = ', '.join(f'"{v}"' if isinstance(v, str) else str(v) for v in value)
                f.write(f'{key} = [{items}]\n')
            elif isinstance(value, bool):
                f.write(f'{key} = {str(value).lower()}\n')
            else:
                f.write(f'{key} = {value}\n')

    # Then write nested sections
    for key, value in data.items():
        if isinstance(value, dict):
            section_path = path + [key]
            f.write(f'\n[{".".join(section_path)}]\n')
            _write_toml_section(f, value, section_path)


def install():
    """
    Install Engram MCP server into Claude Code, Claude Desktop, Cursor, and Codex configurations.
    """
    print("🧠 Engram Memory Layer - MCP Installer")
    print("=======================================")
    
    # 1. Determine configuration
    # We use sys.executable to ensure we use the python that has engram installed
    # This ensures that when the MCP server runs, it has access to the 'engram' package
    mcp_config = {
        "command": sys.executable,
        "args": ["-m", "engram.mcp_server"],
        "env": {}
    }
    
    # Try to capture API keys from current environment
    # We want to propagate these to the MCP server configuration if possible
    api_key_names = ["GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"]
    found_keys = {}
    
    # Also check for FADEM_* specific env vars
    for key in os.environ:
        if key in api_key_names or key.startswith("FADEM_"):
            found_keys[key] = os.environ[key]
            
    if found_keys:
        mcp_config["env"] = found_keys
        print(f"✓ Found {len(found_keys)} environment variables to forward: {', '.join(found_keys.keys())}")
    else:
        print("ℹ️  No API keys found in current environment. You may need to add them manually to the config files later.")

    # 2. Target Configuration Files
    # JSON-based configs (Claude Code, Claude Desktop, Cursor)
    json_targets = [
        {
            "name": "Claude Code (CLI)",
            "path": Path.home() / ".claude.json",
            "agent_id": "claude-code",
        },
        {
            "name": "Claude Desktop (macOS)",
            "path": Path.home() / "Library/Application Support/Claude/claude_desktop_config.json",
            "agent_id": "claude-code",
        },
        {
            "name": "Cursor",
            "path": Path.home() / ".cursor" / "mcp.json",
            "agent_id": "cursor",
        },
    ]

    # TOML-based configs (Codex)
    toml_targets = [
        {
            "name": "Codex CLI",
            "path": Path.home() / ".codex" / "config.toml",
            "agent_id": "codex",
        },
    ]

    installed_count = 0

    # Install to JSON configs
    for target in json_targets:
        target_config = _config_with_agent_identity(mcp_config, target["agent_id"])
        if _update_config(target["name"], target["path"], "engram-memory", target_config):
            installed_count += 1

    # Install to TOML configs (Codex)
    for target in toml_targets:
        target_config = _config_with_agent_identity(mcp_config, target["agent_id"])
        if _update_codex_config(target["name"], target["path"], "engram-memory", target_config):
            installed_count += 1

    # Install OpenClaw skill
    print("\nChecking OpenClaw...")
    from engram_enterprise.integrations.openclaw import deploy as _deploy_openclaw
    if _deploy_openclaw():
        installed_count += 1

    # Install Claude Code plugin
    print("\nDeploying Claude Code plugin...")
    from engram_enterprise.integrations.claude_code import deploy as _deploy_cc_plugin
    if _deploy_cc_plugin():
        installed_count += 1

    # Install workspace-level continuity rules for agents (idempotent block updates)
    skip_workspace_rules = os.environ.get("ENGRAM_INSTALL_SKIP_WORKSPACE_RULES", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not skip_workspace_rules:
        cwd = Path.cwd()
        if (cwd / ".git").exists() or (cwd / "pyproject.toml").exists():
            updated_paths = _install_workspace_continuity_rules(cwd)
            if updated_paths:
                print("\nInstalled workspace continuity rules:")
                for entry in updated_paths:
                    print(f"  • {entry}")
            else:
                print("\nWorkspace continuity rules already up to date.")
        else:
            print("\nℹ️  Skipping workspace continuity rules (current directory is not a project root).")
    else:
        print("\nℹ️  Skipping workspace continuity rules (ENGRAM_INSTALL_SKIP_WORKSPACE_RULES is set).")

    if installed_count > 0:
        print("\n✨ Installation successful!")
        print("Engram is now configured for:")
        print("  • Claude Code (claude CLI)")
        print("  • Claude Desktop")
        print("  • Cursor")
        print("  • OpenAI Codex CLI")
        print("  • OpenClaw")
        print("  • Claude Code plugin (hooks + /engram commands + skill)")
        print("\nPlease restart your agent/IDE to load the new MCP server.")
        print("To verify, ask your agent: 'What memory tools do you have?'")
    else:
        print("\n⚠️  No configuration files were updated.")
        print("Make sure you have Claude Code, Claude Desktop, Cursor, or Codex installed.")

def _update_config(name: str, path: Path, server_name: str, server_config: Dict[str, Any]) -> bool:
    """
    Update a specific configuration file with the MCP server details.
    Returns True if an update happened or was already correct.
    """
    # Check if parent directory exists
    if not path.parent.exists():
        # For paths in home directory (like ~/.cursor), create the directory
        # For paths in Application Support, lack of dir means app not installed
        if path.parent.parent == Path.home():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f"  ❌ Could not create {path.parent}: {e}")
                return False
        else:
            return False

    print(f"\nChecking {name} config...")
    
    data = {}
    file_exists = path.exists()
    
    if file_exists:
        try:
            with open(path, 'r') as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
        except json.JSONDecodeError:
            print(f"  ❌ Error: Could not parse existing JSON at {path}. Skipping.")
            return False
        except Exception as e:
            print(f"  ❌ Error reading {path}: {e}")
            return False

    if "mcpServers" not in data:
        data["mcpServers"] = {}
        
    existing = data["mcpServers"].get(server_name)
    
    # Prepare the new entry
    new_entry = server_config.copy()
    
    # If explicit keys are missing in new config but present in old, preserve them?
    # No, usually we want to control the config. But we should be careful about overwriting env vars user manually added.
    
    should_write = False
    
    if existing:
        # Check if basic command matches
        if existing.get("command") == new_entry["command"] and \
           existing.get("args") == new_entry["args"]:
            
            # Check Env
            existing_env = existing.get("env", {})
            new_env = new_entry.get("env", {})
            
            # Merge: If we found keys in environment, update them. 
            # If we didn't find them, but they exist in config, keep them.
            merged_env = existing_env.copy()
            env_updated = False
            for k, v in new_env.items():
                if merged_env.get(k) != v:
                    merged_env[k] = v
                    env_updated = True
            
            if not env_updated:
                print(f"  ✓ {server_name} already correctly configured.")
                return True # Consider it a success
            else:
                new_entry["env"] = merged_env
                should_write = True
                print(f"  ↻ Updating API keys/Environment for {server_name}...")
        else:
            print(f"  ↻ Updating configuration for {server_name}...")
            # Preserve old env vars if we can't find new ones? 
            # If our new env is empty, but old has stuff, keep old stuff.
            if not new_entry.get("env") and existing.get("env"):
                 new_entry["env"] = existing["env"]
            should_write = True
    else:
        print(f"  + Adding {server_name} to config...")
        should_write = True

    if should_write:
        data["mcpServers"][server_name] = new_entry
        
        # Backup
        if file_exists:
            backup_path = str(path) + ".bak"
            try:
                shutil.copy2(path, backup_path)
            except Exception as e:
                print(f"  ⚠️  Could not create backup: {e}")

        try:
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"  ✓ Saved to {path}")
            return True
        except Exception as e:
            print(f"  ❌ Error writing to {path}: {e}")
            return False
            
    return True

def _update_codex_config(name: str, path: Path, server_name: str, server_config: Dict[str, Any]) -> bool:
    """
    Update Codex TOML configuration file with the MCP server details.
    Returns True if an update happened or was already correct.
    """
    # Check if .codex directory exists (create if not)
    if not path.parent.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"  ❌ Could not create {path.parent}: {e}")
            return False

    print(f"\nChecking {name} config...")

    file_exists = path.exists()
    data = {}

    if file_exists:
        try:
            data = _read_toml(path)
        except Exception as e:
            print(f"  ❌ Error reading {path}: {e}")
            return False

    # Initialize mcp_servers section if needed
    if "mcp_servers" not in data:
        data["mcp_servers"] = {}

    existing = data["mcp_servers"].get(server_name)

    # Prepare new entry in Codex format
    new_entry = {
        "command": server_config["command"],
        "args": server_config["args"],
    }
    if server_config.get("env"):
        new_entry["env"] = server_config["env"]

    should_write = False

    if existing:
        if existing.get("command") == new_entry["command"] and \
           existing.get("args") == new_entry["args"]:
            # Check env
            existing_env = existing.get("env", {})
            new_env = new_entry.get("env", {})

            merged_env = existing_env.copy()
            env_updated = False
            for k, v in new_env.items():
                if merged_env.get(k) != v:
                    merged_env[k] = v
                    env_updated = True

            if not env_updated:
                print(f"  ✓ {server_name} already correctly configured.")
                return True
            else:
                new_entry["env"] = merged_env
                should_write = True
                print(f"  ↻ Updating API keys/Environment for {server_name}...")
        else:
            print(f"  ↻ Updating configuration for {server_name}...")
            if not new_entry.get("env") and existing.get("env"):
                new_entry["env"] = existing["env"]
            should_write = True
    else:
        print(f"  + Adding {server_name} to config...")
        should_write = True

    if should_write:
        data["mcp_servers"][server_name] = new_entry

        # Backup
        if file_exists:
            backup_path = str(path) + ".bak"
            try:
                shutil.copy2(path, backup_path)
            except Exception as e:
                print(f"  ⚠️  Could not create backup: {e}")

        try:
            _write_toml(path, data)
            print(f"  ✓ Saved to {path}")
            return True
        except Exception as e:
            print(f"  ❌ Error writing to {path}: {e}")
            return False

    return True


if __name__ == "__main__":
    install()
