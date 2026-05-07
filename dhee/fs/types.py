"""Common types for DheeFS.

DheeFS is a virtual information space, not a native filesystem. The types
below keep CLI, MCP, and Python SDK callers on the same structured contract
while still producing shell-friendly text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


class DheeFSError(Exception):
    """Base error for virtual DheeFS operations."""


class DheeFSCommandError(DheeFSError, ValueError):
    """Raised when a command cannot be parsed or executed."""


class DheeFSNotFoundError(DheeFSError, FileNotFoundError):
    """Raised when a virtual path cannot be resolved."""


class DheeFSUnsupportedError(DheeFSError, NotImplementedError):
    """Raised when a mount does not support an operation."""


@dataclass
class DheeFSEntry:
    """A stable virtual directory entry."""

    name: str
    path: str
    kind: str = "file"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def display_name(self) -> str:
        return f"{self.name}/" if self.kind == "dir" and not self.name.endswith("/") else self.name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "metadata": dict(self.metadata or {}),
        }


@dataclass
class DheeFSResult:
    """Result shared by the CLI, MCP tool, and Python SDK."""

    command: str
    stdout: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "data": dict(self.data or {}),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


class DheeMount:
    """Base interface for a DheeFS mount."""

    prefix = "/"

    def __init__(self, workspace: Any):
        self.workspace = workspace

    def list(self, path: str) -> List[DheeFSEntry]:
        raise DheeFSUnsupportedError(f"{self.prefix} does not support ls")

    def read(self, path: str) -> str:
        raise DheeFSUnsupportedError(f"{self.prefix} does not support cat")

    def write(self, path: str, content: str, *, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raise DheeFSUnsupportedError(f"{self.prefix} is read-only")

    def search(self, path: str, query: str) -> List[Dict[str, Any]]:
        text = self.read(path)
        matches: List[Dict[str, Any]] = []
        needle = str(query or "").lower()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not needle or needle in line.lower():
                matches.append({"path": path, "line": lineno, "text": line})
        return matches

    def explain(self, path: str) -> str:
        return self.read(path)


def entries_to_text(entries: Iterable[DheeFSEntry]) -> str:
    names = [entry.display_name() for entry in entries]
    return "\n".join(names)
