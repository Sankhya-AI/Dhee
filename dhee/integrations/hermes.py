"""Dhee onboarding helpers for Hermes Agent."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from dhee.configs.base import _dhee_data_dir
from dhee.core.learnings import LearningExchange


def hermes_home(path: Optional[str] = None) -> Path:
    return Path(path or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def detect_hermes(hermes_home_path: Optional[str] = None) -> Dict[str, Any]:
    """Detect a local Hermes Agent installation without importing Hermes."""
    home = hermes_home(hermes_home_path)
    binary = shutil.which("hermes")
    config_path = home / "config.yaml"
    state_db = home / "state.db"
    installed = bool(binary or config_path.exists() or (home / "hermes-agent").exists())
    memories = [p for p in [home / "SOUL.md", home / "MEMORY.md", home / "USER.md", home / "memories" / "MEMORY.md", home / "memories" / "USER.md"] if p.exists()]
    skill_count = len(list((home / "skills").glob("*/SKILL.md"))) if (home / "skills").exists() else 0
    session_count = len(list((home / "sessions").glob("session_*.json"))) if (home / "sessions").exists() else 0
    config = _read_yaml(config_path)
    memory = config.get("memory")
    active_provider = memory.get("provider") if isinstance(memory, dict) else None
    return {
        "installed": installed,
        "binary": binary,
        "hermes_home": str(home),
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "active_provider": active_provider,
        "state_db": str(state_db),
        "state_db_exists": state_db.exists(),
        "memory_files": [str(p) for p in memories],
        "agent_skill_count": skill_count,
        "session_count": session_count,
    }


def install_provider(
    hermes_home_path: Optional[str] = None,
    enable: bool = False,
    dhee_data_dir: Optional[str] = None,
    offline: bool = False,
    sync_on_start: bool = False,
    sync_existing: bool = False,
    promote_imported: bool = False,
) -> Dict[str, Any]:
    """Install the Dhee Hermes memory provider scaffold."""
    home = hermes_home(hermes_home_path)
    plugin_dir = _provider_plugin_dir(home)
    plugin_dir.mkdir(parents=True, exist_ok=True)

    init_path = plugin_dir / "__init__.py"
    plugin_yaml = plugin_dir / "plugin.yaml"
    readme_path = plugin_dir / "README.md"
    config_path = home / "dhee.json"

    changed = False
    changed |= _write_text_if_changed(
        init_path,
        "from dhee.integrations.hermes_provider import DheeHermesMemoryProvider, register\n"
        "\n"
        "__all__ = ['DheeHermesMemoryProvider', 'register']\n",
    )
    changed |= _write_text_if_changed(
        plugin_yaml,
        "name: dhee\n"
        "version: 1.0.0\n"
        "description: Dhee shared learning and memory provider\n"
        "hooks:\n"
        "  - prefetch\n"
        "  - queue_prefetch\n"
        "  - sync_turn\n"
        "  - on_memory_write\n"
        "  - on_pre_compress\n"
        "  - on_session_end\n",
    )
    changed |= _write_text_if_changed(
        readme_path,
        "# Dhee Hermes Memory Provider\n\n"
        "This provider mirrors Hermes memory writes into Dhee and exposes promoted "
        "Dhee learnings back to Hermes through the native MemoryProvider lifecycle.\n",
    )
    provider_config = {
        "dhee_data_dir": str(Path(dhee_data_dir or _dhee_data_dir()).expanduser()),
        "offline": bool(offline),
        "sync_on_start": bool(sync_on_start),
    }
    changed |= _write_text_if_changed(
        config_path,
        json.dumps(provider_config, indent=2, sort_keys=True) + "\n",
    )

    enabled = False
    backup_path = None
    hermes_config_path = home / "config.yaml"
    if enable:
        config = _read_yaml(hermes_config_path)
        memory = config.get("memory")
        if not isinstance(memory, dict):
            memory = {}
        if memory.get("provider") != "dhee":
            backup_path = _backup_file(hermes_config_path)
            memory["provider"] = "dhee"
            config["memory"] = memory
            hermes_config_path.parent.mkdir(parents=True, exist_ok=True)
            hermes_config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            changed = True
        enabled = True

    sync_result = None
    if sync_existing:
        sync_result = sync_hermes(
            hermes_home_path=str(home),
            user_id="default",
            dry_run=False,
            dhee_data_dir=dhee_data_dir,
            promote=promote_imported,
        )

    return {
        "installed": True,
        "enabled": enabled,
        "hermes_home": str(home),
        "plugin_dir": str(plugin_dir),
        "legacy_plugin_dir": str(_legacy_provider_plugin_dir(home)),
        "provider_config": str(config_path),
        "hermes_config": str(hermes_config_path),
        "backup": str(backup_path) if backup_path else None,
        "sync": sync_result,
        "changed": bool(changed or ((sync_result or {}).get("imported_count", 0) > 0)),
    }


def provider_status(hermes_home_path: Optional[str] = None) -> Dict[str, Any]:
    home = hermes_home(hermes_home_path)
    plugin_dir = _provider_plugin_dir(home)
    legacy_plugin_dir = _legacy_provider_plugin_dir(home)
    config_path = home / "config.yaml"
    provider_config = home / "dhee.json"
    config = _read_yaml(config_path)
    active_provider = None
    memory = config.get("memory")
    if isinstance(memory, dict):
        active_provider = memory.get("provider")
    data_dir = _dhee_data_dir()
    if provider_config.exists():
        try:
            provider_values = json.loads(provider_config.read_text(encoding="utf-8"))
            data_dir = str(provider_values.get("dhee_data_dir") or data_dir)
        except Exception:
            pass
    learning_path = Path(data_dir).expanduser() / "learnings" / "learnings.jsonl"
    return {
        "hermes_home": str(home),
        "plugin_installed": (plugin_dir / "__init__.py").exists() and (plugin_dir / "plugin.yaml").exists(),
        "plugin_dir": str(plugin_dir),
        "legacy_plugin_installed": (legacy_plugin_dir / "__init__.py").exists(),
        "legacy_plugin_dir": str(legacy_plugin_dir),
        "active_provider": active_provider,
        "enabled": active_provider == "dhee",
        "dhee_data_dir": data_dir,
        "provider_config": str(provider_config),
        "provider_config_exists": provider_config.exists(),
        "last_sync": learning_path.stat().st_mtime if learning_path.exists() else None,
        "learning_store": str(learning_path),
    }


def _provider_plugin_dir(home: Path) -> Path:
    """Canonical Hermes MemoryProvider plugin location."""
    return home / "plugins" / "memory" / "dhee"


def _legacy_provider_plugin_dir(home: Path) -> Path:
    """Previous Dhee pre-release install location, kept visible for migration checks."""
    return home / "plugins" / "dhee"


def disable_provider(hermes_home_path: Optional[str] = None) -> Dict[str, Any]:
    home = hermes_home(hermes_home_path)
    config_path = home / "config.yaml"
    config = _read_yaml(config_path)
    memory = config.get("memory")
    changed = False
    backup_path = None
    if isinstance(memory, dict) and memory.get("provider") == "dhee":
        backup_path = _backup_file(config_path)
        memory["provider"] = ""
        config["memory"] = memory
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        changed = True
    return {
        "disabled": changed,
        "hermes_home": str(home),
        "hermes_config": str(config_path),
        "backup": str(backup_path) if backup_path else None,
    }


def sync_hermes(
    hermes_home_path: Optional[str] = None,
    repo: Optional[str] = None,
    user_id: str = "default",
    dry_run: bool = False,
    dhee_data_dir: Optional[str] = None,
    promote: bool = False,
) -> Dict[str, Any]:
    home = hermes_home(hermes_home_path)
    exchange = LearningExchange(Path(dhee_data_dir or _dhee_data_dir()).expanduser() / "learnings")
    return exchange.import_hermes_home(
        home,
        user_id=user_id,
        source_agent_id="hermes",
        repo=repo,
        dry_run=dry_run,
        promote=promote,
    )


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak-{stamp}")
    shutil.copy2(str(path), str(backup))
    return backup


def _write_text_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")
    return True
