"""Installer artifact cleanup for ``dhee uninstall``.

The curl installer intentionally touches a tiny set of user-owned files:
managed symlinks in ``~/.local/bin`` and a marked ``# dhee`` PATH block in a
shell profile.  Uninstall must be equally precise: remove Dhee-owned artifacts
without guessing at, or rewriting, user-managed shell configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

MANAGED_BINARIES = ("dhee", "dhee-mcp", "dhee-mcp-full", "engram-bus")
PROFILE_RELATIVE_PATHS = (
    ".zshrc",
    ".bashrc",
    ".profile",
    ".config/fish/config.fish",
)


def installer_bin_dir(*, home: Path | None = None) -> Path:
    """Return the bin directory used by the curl installer."""
    root = Path.home() if home is None else Path(home)
    return root / ".local" / "bin"


def managed_venv_dir(config_dir: str | os.PathLike[str]) -> Path:
    """Return the managed virtualenv directory under Dhee's data dir."""
    return Path(config_dir).expanduser() / ".venv"


def cleanup_install_artifacts(
    config_dir: str | os.PathLike[str],
    *,
    home: Path | None = None,
    bin_dir: Path | None = None,
) -> dict[str, Any]:
    """Remove installer-owned shell/profile artifacts.

    The function is safe to call repeatedly and when ``config_dir`` no longer
    exists. It never removes real executables, user PATH edits, or symlinks that
    target anything outside Dhee's managed venv.
    """
    resolved_bin_dir = installer_bin_dir(home=home) if bin_dir is None else Path(bin_dir)
    return {
        "symlinks": cleanup_installer_symlinks(
            config_dir,
            home=home,
            bin_dir=resolved_bin_dir,
        ),
        "shell_profiles": cleanup_shell_profiles(
            home=home,
            bin_dir=resolved_bin_dir,
        ),
    }


def cleanup_installer_symlinks(
    config_dir: str | os.PathLike[str],
    *,
    home: Path | None = None,
    bin_dir: Path | None = None,
    names: Iterable[str] = MANAGED_BINARIES,
) -> dict[str, Any]:
    """Remove managed symlinks that point into ``<config_dir>/.venv/bin``."""
    resolved_bin_dir = installer_bin_dir(home=home) if bin_dir is None else Path(bin_dir)
    managed_bin = (managed_venv_dir(config_dir) / "bin").resolve(strict=False)
    removed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for name in names:
        path = resolved_bin_dir / name
        if not path.exists() and not path.is_symlink():
            continue
        if not path.is_symlink():
            skipped.append({"name": name, "path": str(path), "reason": "not_symlink"})
            continue

        target = _symlink_target(path)
        if _is_relative_to(target, managed_bin):
            path.unlink()
            removed.append({"name": name, "path": str(path), "target": str(target)})
        else:
            skipped.append(
                {
                    "name": name,
                    "path": str(path),
                    "target": str(target),
                    "reason": "outside_managed_venv",
                }
            )

    return {
        "bin_dir": str(resolved_bin_dir),
        "managed_bin": str(managed_bin),
        "removed": removed,
        "skipped": skipped,
    }


def cleanup_shell_profiles(
    *,
    home: Path | None = None,
    bin_dir: Path | None = None,
) -> dict[str, Any]:
    """Remove exact Dhee installer PATH blocks from common shell profiles."""
    root = Path.home() if home is None else Path(home)
    resolved_bin_dir = installer_bin_dir(home=root) if bin_dir is None else Path(bin_dir)
    expected_path_lines = {
        f'export PATH="{resolved_bin_dir}:$PATH"',
        f"fish_add_path {resolved_bin_dir}",
    }
    changed: list[dict[str, Any]] = []
    scanned: list[str] = []

    for rel_path in PROFILE_RELATIVE_PATHS:
        profile = root / rel_path
        scanned.append(str(profile))
        if not profile.exists() or not profile.is_file():
            continue
        original = profile.read_text(encoding="utf-8")
        updated, removed_blocks = _remove_dhee_profile_blocks(original, expected_path_lines)
        if removed_blocks:
            profile.write_text(updated, encoding="utf-8")
            changed.append({"path": str(profile), "removed_blocks": removed_blocks})

    return {"bin_dir": str(resolved_bin_dir), "changed": changed, "scanned": scanned}


def _remove_dhee_profile_blocks(content: str, expected_path_lines: set[str]) -> tuple[str, int]:
    lines = content.splitlines(keepends=True)
    updated: list[str] = []
    removed_blocks = 0
    i = 0
    while i < len(lines):
        current = _strip_newline(lines[i])
        next_line = _strip_newline(lines[i + 1]) if i + 1 < len(lines) else None
        if current == "# dhee" and next_line in expected_path_lines:
            if updated and not updated[-1].strip():
                updated.pop()
            removed_blocks += 1
            i += 2
            continue
        updated.append(lines[i])
        i += 1
    return "".join(updated), removed_blocks


def _strip_newline(value: str) -> str:
    return value.rstrip("\r\n")


def _symlink_target(path: Path) -> Path:
    raw_target = os.readlink(path)
    target = Path(raw_target)
    if not target.is_absolute():
        target = path.parent / target
    return target.resolve(strict=False)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
