"""Typed facade over Dhee's local context substrate.

This is intentionally small.  The goal is to give CLI, MCP, daemon, and future
SDK code one stable boundary for context inspection without turning Dhee into a
generic context database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dhee.fs import ContextWorkspace, DheeFSEntry


@dataclass(frozen=True)
class KernelScope:
    repo: Optional[str] = None
    user_id: str = "default"
    agent_id: str = "kernel"
    workspace_id: Optional[str] = None

    @property
    def resolved_repo(self) -> Optional[str]:
        if self.repo:
            return os.path.abspath(os.path.expanduser(self.repo))
        return None

    @property
    def resolved_workspace_id(self) -> str:
        return self.workspace_id or self.resolved_repo or os.getcwd()


class DheeContextKernel:
    """Decision-complete context boundary for local developer-agent workflows."""

    def __init__(self, scope: Optional[KernelScope] = None, *, db: Any = None):
        self.scope = scope or KernelScope()
        self.db = db

    def workspace(self) -> ContextWorkspace:
        return ContextWorkspace(
            repo=self.scope.resolved_repo,
            user_id=self.scope.user_id,
            agent_id=self.scope.agent_id,
            workspace_id=self.scope.resolved_workspace_id,
            db=self.db,
        )

    def normalize(self, uri_or_path: str) -> str:
        return self.workspace().normalize_path(uri_or_path)

    def list(self, uri_or_path: str = "/") -> List[DheeFSEntry]:
        return self.workspace().list(uri_or_path)

    def read(self, uri_or_path: str) -> str:
        return self.workspace().read(uri_or_path)

    def search(self, uri_or_path: str, query: str) -> List[Dict[str, Any]]:
        return self.workspace().search(uri_or_path, query)

    def snapshot(self) -> Dict[str, Any]:
        ws = self.workspace()
        return {
            "scope": {
                "repo": self.scope.resolved_repo,
                "user_id": self.scope.user_id,
                "agent_id": self.scope.agent_id,
                "workspace_id": self.scope.resolved_workspace_id,
            },
            "state": ws.context_state_store().status(),
            "handoff": ws.handoff_snapshot(),
            "shared": ws.shared_snapshot(),
        }


__all__ = ["DheeContextKernel", "KernelScope"]
