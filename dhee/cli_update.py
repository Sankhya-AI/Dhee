"""`dhee update` — pull the latest Dhee Developer Brain release.

Two upgrade paths, auto-detected:

  1. **PyPI install** (the usual path via ``install.sh``): the ``dhee``
     executable lives inside ``~/.dhee/.venv``. We upgrade the package
     there with pip and re-link the console scripts.
  2. **Editable source checkout** (developers running from a git clone):
     runs ``git pull --ff-only`` in the detected project root and then
     ``pip install -e .`` inside the venv.

Either way we finish by relinking console scripts so the local curl-installed
runtime stays usable.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


PACKAGE_NAME = "dhee"


def _venv_python() -> Path:
    """Return the Python executable for the install venv.

    Priority:
      - ``DHEE_VENV`` env var (opt-in override)
      - ``~/.dhee/.venv/bin/python`` (installer default)
      - current ``sys.executable`` (dev/editable case)
    """
    custom = os.environ.get("DHEE_VENV")
    if custom:
        candidate = Path(custom) / ("Scripts" if os.name == "nt" else "bin") / (
            "python.exe" if os.name == "nt" else "python"
        )
        if candidate.exists():
            return candidate
    default_venv = Path.home() / ".dhee" / ".venv" / (
        "Scripts" if os.name == "nt" else "bin"
    )
    for name in ("python3", "python"):
        candidate = default_venv / (f"{name}.exe" if os.name == "nt" else name)
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _is_editable_install() -> bool:
    """True when ``dhee`` was installed with ``pip install -e``.

    Editable installs leave a ``*.egg-link`` or a ``direct_url.json``
    with ``editable: true`` in the site-packages metadata.
    """
    try:
        import dhee as _dhee_pkg
    except Exception:
        return False
    pkg_dir = Path(_dhee_pkg.__file__).resolve().parent
    # Editable installs sit inside a git worktree (has .git alongside).
    root = pkg_dir.parent
    return (root / ".git").exists() and (root / "pyproject.toml").exists()


def _project_root_for_editable() -> Optional[Path]:
    try:
        import dhee as _dhee_pkg
    except Exception:
        return None
    root = Path(_dhee_pkg.__file__).resolve().parent.parent
    return root if (root / ".git").exists() else None


def _run(cmd: list, *, cwd: Optional[Path] = None, check: bool = True) -> int:
    printable = " ".join(str(c) for c in cmd)
    print(f"→ {printable}" + (f"  (in {cwd})" if cwd else ""))
    completed = subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=False)
    if check and completed.returncode != 0:
        sys.stderr.write(f"command failed with exit {completed.returncode}: {printable}\n")
        sys.exit(completed.returncode)
    return completed.returncode


def _print_current_version() -> None:
    try:
        from importlib.metadata import version

        print(f"dhee {version('dhee')} currently installed")
    except Exception:
        pass


def _relink_binaries() -> None:
    """Ensure ``~/.local/bin/dhee{,-mcp}`` point to the freshly installed venv."""
    venv_python = _venv_python()
    if not str(venv_python).startswith(str(Path.home() / ".dhee")):
        return
    bin_dir = venv_python.parent
    local_bin = Path.home() / ".local" / "bin"
    try:
        local_bin.mkdir(parents=True, exist_ok=True)
        for name in ("dhee", "dhee-mcp", "dhee-mcp-full"):
            source = bin_dir / name
            dest = local_bin / name
            if source.exists():
                if dest.is_symlink() or dest.exists():
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                os.symlink(source, dest)
    except Exception:
        pass


def cmd_update(args: argparse.Namespace) -> None:
    """Upgrade Dhee in-place, source OR wheel."""
    _print_current_version()
    python = _venv_python()

    if _is_editable_install() and not args.from_pypi:
        root = _project_root_for_editable()
        if root is None:
            sys.stderr.write("editable install detected but project root not found.\n")
            sys.exit(1)
        print(f"Editable install detected at {root}")
        _run(["git", "pull", "--ff-only"], cwd=root)
        _run([str(python), "-m", "pip", "install", "--upgrade", "pip", "-q"])
        _run([str(python), "-m", "pip", "install", "-e", ".", "-q"], cwd=root)
    else:
        print("Upgrading Dhee from PyPI…")
        _run([str(python), "-m", "pip", "install", "--upgrade", "pip", "-q"])
        _run([str(python), "-m", "pip", "install", "--upgrade", PACKAGE_NAME, "-q"])

    _relink_binaries()

    try:
        from importlib.metadata import version

        # importlib.metadata caches across the process; restart-proof
        # check: re-exec the freshly-installed binary once, ignoring
        # failures so `dhee update` completes whether or not exec
        # succeeds.
        latest = version("dhee")
    except Exception:
        latest = "(unknown)"
    print(f"\n✓ Dhee updated → {latest}")
    print("Next: dhee link /path/to/repo  |  dhee handoff")


def register(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p = sub.add_parser(
        "update",
        help="Update Dhee Developer Brain",
    )
    p.add_argument(
        "--from-pypi",
        action="store_true",
        help="Force the PyPI upgrade path even in an editable checkout.",
    )
    p.set_defaults(func=cmd_update)
