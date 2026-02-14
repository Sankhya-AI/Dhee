"""Provenance helpers for Engram v2."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class Provenance:
    source_type: str = "mcp"
    source_app: Optional[str] = None
    source_event_id: Optional[str] = None
    agent_id: Optional[str] = None
    tool: Optional[str] = None
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if not data["created_at"]:
            data["created_at"] = datetime.now(timezone.utc).isoformat()
        return data


def build_provenance(
    *,
    source_type: str = "mcp",
    source_app: Optional[str] = None,
    source_event_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    tool: Optional[str] = None,
) -> Dict[str, Any]:
    return Provenance(
        source_type=source_type,
        source_app=source_app,
        source_event_id=source_event_id,
        agent_id=agent_id,
        tool=tool,
    ).to_dict()
