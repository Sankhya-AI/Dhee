"""SkillLoader — load skills from Python modules."""

from __future__ import annotations

import importlib
import inspect
import logging
from typing import Any, Callable

from engram_skills.skill import Skill

logger = logging.getLogger(__name__)


def load_skills_from_module(module_path: str) -> list[Skill]:
    """Load skills from a Python module.

    Discovers functions decorated with @skill or having a __skill__ attribute.
    Falls back to loading all public functions.

    Args:
        module_path: Dotted module path (e.g. "mypackage.tools")

    Returns:
        List of Skill objects found in the module.
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        logger.error("Failed to import module '%s': %s", module_path, e)
        return []

    skills = []

    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if name.startswith("_"):
            continue

        # Check for __skill__ marker
        skill_meta = getattr(obj, "__skill__", None)

        if skill_meta and isinstance(skill_meta, dict):
            skill = Skill(
                name=skill_meta.get("name", name),
                description=skill_meta.get("description", obj.__doc__ or ""),
                parameters=skill_meta.get("parameters", _extract_params(obj)),
                examples=skill_meta.get("examples", []),
                tags=skill_meta.get("tags", []),
                callable=obj,
            )
        else:
            # Auto-discover: use docstring and signature
            skill = Skill(
                name=name,
                description=obj.__doc__ or f"Function {name}",
                parameters=_extract_params(obj),
                callable=obj,
            )

        skills.append(skill)

    logger.info("Loaded %d skills from module '%s'", len(skills), module_path)
    return skills


def _extract_params(fn: Callable) -> dict[str, str]:
    """Extract parameter names and type annotations from a function."""
    params = {}
    sig = inspect.signature(fn)
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        annotation = param.annotation
        if annotation != inspect.Parameter.empty:
            params[pname] = annotation.__name__ if hasattr(annotation, "__name__") else str(annotation)
        else:
            params[pname] = "any"
    return params


def skill(name: str = "", description: str = "", tags: list[str] | None = None,
          examples: list[str] | None = None) -> Callable:
    """Decorator to mark a function as a discoverable skill.

    Usage::

        @skill(name="run_tests", description="Run pytest", tags=["testing"])
        def run_tests(path: str = "tests/", verbose: bool = False):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        fn.__skill__ = {
            "name": name or fn.__name__,
            "description": description or fn.__doc__ or "",
            "tags": tags or [],
            "examples": examples or [],
            "parameters": _extract_params(fn),
        }
        return fn
    return decorator
