"""Pydantic models for Dhee's universal agent runtime."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Patch(BaseModel):
    """Context patch returned before an agent run starts."""

    run_id: str
    user_id: str
    app_id: str
    context: str
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Voice- and agent-friendly result from the memory tool surface."""

    ok: bool = True
    speakable_summary: Optional[str] = None
    result: dict[str, Any] = Field(default_factory=dict)
