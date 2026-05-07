"""Future external context-source contract for DheeFS.

Context sources are read-first adapters for tools like Slack, Gmail, and
Notion. They ingest/search evidence for Dhee artifacts and learnings; they are
not a generic remote action filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dhee.fs.types import DheeFSEntry, DheeFSUnsupportedError, DheeMount


@dataclass
class ContextSourceConfig:
    name: str
    provider: str
    prefix: str
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    secret_ref: Optional[str] = None


@dataclass
class ContextItem:
    id: str
    title: str
    body: str
    source: str
    uri: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "source": self.source,
            "uri": self.uri,
            "metadata": dict(self.metadata or {}),
        }


class ContextSourceMount(DheeMount):
    """Read-only base class for optional Slack/Gmail/Notion-style mounts."""

    def __init__(self, workspace: Any, config: ContextSourceConfig):
        super().__init__(workspace)
        self.config = config
        self.name = config.name
        prefix = (config.prefix or "").strip().rstrip("/")
        if not prefix:
            prefix = f"/sources/{config.name}"
        elif not prefix.startswith("/"):
            prefix = f"/sources/{prefix}"
        self.prefix = prefix

    def sync(self) -> Dict[str, Any]:
        return {"ok": True, "items_synced": 0, "provider": self.config.provider}

    def list(self, path: str) -> List[DheeFSEntry]:
        return []

    def read(self, path: str) -> str:
        raise DheeFSUnsupportedError(f"{self.prefix} has no readable item at {path}")

    def search(self, path_or_query: str, query: Optional[str] = None) -> List[ContextItem]:  # type: ignore[override]
        needle = query if query is not None else path_or_query
        if not needle:
            return []
        return []
