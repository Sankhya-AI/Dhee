"""Helpers for locating local Dhee model artifacts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional


_DEFAULT_MODEL_DIR = Path.home() / ".dhee" / "models"
_PREFERRED_MODEL_NAMES = (
    "dhee-qwen3.5-2b-q4_k_m.gguf",
    "dhee-qwen3.5-0.8b-q4_k_m.gguf",
)


def get_default_model_dir() -> str:
    """Return the default directory used for local GGUF artifacts."""
    return str(_DEFAULT_MODEL_DIR)


def iter_preferred_model_paths(model_dir: Optional[str] = None) -> Iterable[Path]:
    """Yield preferred model filenames in priority order."""
    base_dir = Path(model_dir) if model_dir else _DEFAULT_MODEL_DIR
    for name in _PREFERRED_MODEL_NAMES:
        yield base_dir / name


def resolve_model_path(
    explicit_path: Optional[str] = None,
    model_dir: Optional[str] = None,
) -> str:
    """Resolve the best local model path.

    Priority:
    1. Explicit model path, unless it is empty or "auto"
    2. `DHEE_MODEL_PATH` environment variable
    3. Preferred built-in filenames (2B first, then 0.8B)
    4. Most recently modified `dhee-*.gguf` artifact
    5. Default preferred 2B filename, even if it does not exist yet
    """
    if explicit_path and explicit_path != "auto":
        return explicit_path

    env_path = os.environ.get("DHEE_MODEL_PATH", "").strip()
    if env_path:
        return env_path

    base_dir = Path(model_dir) if model_dir else _DEFAULT_MODEL_DIR

    for candidate in iter_preferred_model_paths(str(base_dir)):
        if candidate.exists():
            return str(candidate)

    discovered = sorted(
        base_dir.glob("dhee-*.gguf"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if discovered:
        return str(discovered[0])

    return str(base_dir / _PREFERRED_MODEL_NAMES[0])
