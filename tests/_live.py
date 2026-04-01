"""Shared helpers for opt-in live integration tests."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

LIVE_TESTS_ENV = "DHEE_RUN_LIVE_TESTS"
NVIDIA_KEYS = (
    "NVIDIA_API_KEY",
    "NVIDIA_EMBEDDING_API_KEY",
    "NVIDIA_QWEN_API_KEY",
    "LLAMA_API_KEY",
)


def load_project_env() -> None:
    """Populate environment variables from the repo-root .env if present."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _missing_optional_packages(packages: tuple[str, ...]) -> list[str]:
    missing = []
    for package in packages:
        try:
            importlib.import_module(package)
        except ImportError:
            missing.append(package)
    return missing


def require_live_nvidia_tests(*packages: str) -> None:
    """Skip the current pytest module unless live NVIDIA tests are explicitly enabled."""
    load_project_env()

    if os.getenv(LIVE_TESTS_ENV) != "1":
        pytest.skip(
            f"set {LIVE_TESTS_ENV}=1 to run live NVIDIA integration tests",
            allow_module_level=True,
        )

    if not any(os.environ.get(key) for key in NVIDIA_KEYS):
        pytest.skip("requires NVIDIA API credentials", allow_module_level=True)

    missing = _missing_optional_packages(tuple(packages))
    if missing:
        pytest.skip(
            f"requires optional dependencies: {', '.join(sorted(missing))}",
            allow_module_level=True,
        )


def ensure_live_nvidia_runtime(*packages: str) -> None:
    """Raise a runtime error when a live suite is executed without prerequisites."""
    load_project_env()

    problems = []
    if os.getenv(LIVE_TESTS_ENV) != "1":
        problems.append(f"set {LIVE_TESTS_ENV}=1")
    if not any(os.environ.get(key) for key in NVIDIA_KEYS):
        problems.append("provide NVIDIA API credentials")

    missing = _missing_optional_packages(tuple(packages))
    if missing:
        problems.append(f"install optional dependencies: {', '.join(sorted(missing))}")

    if problems:
        raise RuntimeError("Cannot run live NVIDIA suite: " + "; ".join(problems))
