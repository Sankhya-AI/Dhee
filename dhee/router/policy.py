"""Tunable digest-depth policy per (tool, intent).

A single JSON file at ``~/.dhee/router_policy.json`` holds:

    {
      "version": 1,
      "updated_at": <unix>,
      "depths": {
        "Read": {"source_code": "normal", "test": "shallow", ...},
        "Bash": {"git_log": "deep", ...},
        "Agent": {"code-review": "normal", ...}
      }
    }

Handlers call ``get_depth(tool, intent)`` at request time. Missing keys
fall back to ``"normal"`` so first-run behavior is unchanged.

Writes go through ``set_depth`` (atomic tempfile+rename). The Phase 8
tuner reads expansion logs, proposes changes, and either prints them
(`--dry-run`, default) or applies them (`--apply`). Never flips a
bucket autonomously in the hot path — that would couple observation to
policy and make regressions hard to attribute.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

_DEFAULT_DEPTH = "normal"
_VALID_DEPTHS = ("shallow", "normal", "deep")


def _policy_path() -> Path:
    custom = os.environ.get("DHEE_ROUTER_POLICY")
    if custom:
        return Path(custom).expanduser()
    try:
        from dhee.configs.base import _dhee_data_dir

        base = Path(_dhee_data_dir())
    except Exception:
        base = Path.home() / ".dhee"
    return base / "router_policy.json"


def load() -> dict[str, Any]:
    path = _policy_path()
    if not path.exists():
        return {"version": 1, "updated_at": 0.0, "depths": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "updated_at": 0.0, "depths": {}}
    depths = data.get("depths")
    if not isinstance(depths, dict):
        data["depths"] = {}
    return data


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def get_depth(tool: str, intent: str) -> str:
    """Return tuned depth for (tool, intent) or ``"normal"``.

    Hot-path; never raises. On any error returns the default.
    """
    try:
        data = load()
        tool_map = data.get("depths", {}).get(tool, {})
        depth = tool_map.get(intent) if isinstance(tool_map, dict) else None
        if depth in _VALID_DEPTHS:
            return depth
    except Exception:
        pass
    return _DEFAULT_DEPTH


def set_depth(tool: str, intent: str, depth: str) -> None:
    """Persist a tuned depth. Raises ValueError on bad depth."""
    if depth not in _VALID_DEPTHS:
        raise ValueError(f"depth must be one of {_VALID_DEPTHS}, got {depth!r}")
    data = load()
    depths = data.setdefault("depths", {})
    tool_map = depths.setdefault(tool, {})
    if not isinstance(tool_map, dict):
        depths[tool] = {}
        tool_map = depths[tool]
    tool_map[intent] = depth
    data["updated_at"] = time.time()
    data["version"] = 1
    _atomic_write(_policy_path(), data)


def clear(tool: str | None = None, intent: str | None = None) -> int:
    """Remove tuned entries. Returns count removed."""
    data = load()
    depths = data.get("depths", {})
    if not isinstance(depths, dict):
        return 0
    removed = 0
    if tool is None:
        removed = sum(len(v) for v in depths.values() if isinstance(v, dict))
        data["depths"] = {}
    elif intent is None:
        removed = len(depths.get(tool, {}) or {})
        depths.pop(tool, None)
    else:
        tm = depths.get(tool, {})
        if isinstance(tm, dict) and intent in tm:
            tm.pop(intent)
            removed = 1
    if removed:
        data["updated_at"] = time.time()
        _atomic_write(_policy_path(), data)
    return removed
