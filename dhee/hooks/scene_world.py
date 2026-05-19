"""Optional SceneWorld routing bridge for Dhee hooks and MCP.

SceneWorld lives outside Dhee so Dhee can remain the memory substrate. This
bridge deliberately imports SankhyaWM lazily and only when explicitly enabled.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def _flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def scene_world_enabled() -> bool:
    """Return whether Dhee should call the external SceneWorld router."""

    raw = (
        os.environ.get("DHEE_SCENE_WORLD_ENABLED")
        or os.environ.get("DHEE_SCENE_WORLD")
        or ""
    ).strip().lower()
    if raw == "auto":
        return _discover_project(None) is not None
    return raw in _TRUE_VALUES


def _valid_project(path: Path) -> bool:
    return (path / "sankhya_wm" / "dhee_scene_world_adapter.py").exists()


def _candidate_projects(repo: Optional[str]) -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("DHEE_SCENE_WORLD_PROJECT")
    if explicit:
        candidates.append(Path(explicit).expanduser())

    model_path = os.environ.get("DHEE_SCENE_WORLD_MODEL") or os.environ.get("SCENE_WORLD_MODEL_PATH")
    if model_path:
        model = Path(model_path).expanduser()
        candidates.extend([model.parent.parent, model.parent])

    if repo:
        root = Path(repo).expanduser()
        candidates.extend([root, root.parent / "sankhyaWM"])

    cwd = Path.cwd()
    candidates.extend([cwd, cwd.parent / "sankhyaWM"])

    # Local development layout: /Desktop/Dhee and /Desktop/sankhyaWM siblings.
    here = Path(__file__).resolve()
    if len(here.parents) > 3:
        candidates.append(here.parents[3] / "sankhyaWM")

    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _discover_project(repo: Optional[str]) -> Optional[Path]:
    for candidate in _candidate_projects(repo):
        if _valid_project(candidate):
            return candidate
    return None


def _discover_model_path(repo: Optional[str], project: Optional[Path]) -> Optional[Path]:
    explicit = os.environ.get("DHEE_SCENE_WORLD_MODEL") or os.environ.get("SCENE_WORLD_MODEL_PATH")
    if explicit:
        return Path(explicit).expanduser()
    candidates: list[Path] = []
    if project:
        candidates.append(project / "models" / "scene_world_reward_model.json")
    if repo:
        candidates.append(Path(repo).expanduser() / ".dhee" / "scene_world_reward_model.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _ensure_import_path(project: Optional[Path]) -> None:
    if not project:
        return
    path = str(project)
    if path not in sys.path:
        sys.path.insert(0, path)


def _int_arg(value: Any, *, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def route_task(
    task: str,
    *,
    repo: Optional[str] = None,
    user_id: Optional[str] = None,
    harness: str = "agent",
    top_k: int = 4,
    record: Optional[bool] = None,
) -> Dict[str, Any]:
    """Return a status-wrapped SceneWorld route for a task.

    The shape is stable for hooks and MCP. Errors are returned as data because
    this code runs on hot agent paths and must never break Dhee itself.
    """

    task = str(task or "").strip()
    if not task:
        return {"enabled": scene_world_enabled(), "status": "empty_task"}
    if not scene_world_enabled():
        return {"enabled": False, "status": "disabled"}

    project = _discover_project(repo)
    _ensure_import_path(project)
    model_path = _discover_model_path(repo, project)
    debug = _flag("DHEE_SCENE_WORLD_DEBUG")

    try:
        from sankhya_wm.dhee_scene_world_adapter import predict_next_action
    except Exception as exc:
        result: Dict[str, Any] = {
            "enabled": True,
            "status": "unavailable",
            "reason": f"{type(exc).__name__}: {exc}",
            "project": str(project) if project else None,
        }
        return result

    try:
        route = predict_next_action(
            task,
            user_id=user_id or os.environ.get("DHEE_USER_ID", "default"),
            model_path=model_path,
            model_weight=float(os.environ.get("DHEE_SCENE_WORLD_MODEL_WEIGHT", "0.7")),
            provider=os.environ.get("DHEE_PROVIDER"),
            data_dir=os.environ.get("DHEE_DATA_DIR"),
            top_k=_int_arg(top_k, default=4, lower=1, upper=8),
            record=_flag("DHEE_SCENE_WORLD_RECORD") if record is None else bool(record),
            route_log_path=os.environ.get("DHEE_SCENE_WORLD_ROUTE_LOG"),
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        return {
            "enabled": True,
            "status": "error",
            "reason": error if debug else type(exc).__name__,
            "project": str(project) if project else None,
            "model_path": str(model_path) if model_path else None,
            "harness": harness,
        }

    return {
        "enabled": True,
        "status": "ok",
        "route": route,
        "project": str(project) if project else None,
        "model_path": str(model_path) if model_path else None,
        "harness": harness,
    }


def predict_scene_world_route(
    task: str,
    *,
    repo: Optional[str] = None,
    user_id: Optional[str] = None,
    harness: str = "agent",
    top_k: int = 4,
    record: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Return only the route payload when SceneWorld is enabled and healthy."""

    result = route_task(
        task,
        repo=repo,
        user_id=user_id,
        harness=harness,
        top_k=top_k,
        record=record,
    )
    if result.get("status") == "ok" and isinstance(result.get("route"), dict):
        route = dict(result["route"])
        route.setdefault("_scene_world", {})
        route["_scene_world"].update(
            {
                "project": result.get("project"),
                "model_path": result.get("model_path"),
                "harness": result.get("harness"),
            }
        )
        return route
    return None
