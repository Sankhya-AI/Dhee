"""Pydantic schemas for Engram v2 API."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from engram_enterprise.policy import ALL_CONFIDENTIALITY_SCOPES, DEFAULT_CAPABILITIES

HandoffStatus = Literal["active", "paused", "completed", "abandoned"]


class SessionCreateRequest(BaseModel):
    user_id: str = Field(default="default")
    agent_id: Optional[str] = Field(default=None)
    allowed_confidentiality_scopes: List[str] = Field(default_factory=lambda: ["work"])
    capabilities: List[str] = Field(default_factory=lambda: list(DEFAULT_CAPABILITIES))
    namespaces: List[str] = Field(default_factory=lambda: ["default"])
    ttl_minutes: int = Field(default=24 * 60, ge=1, le=60 * 24 * 30)


class SessionCreateResponse(BaseModel):
    session_id: str
    token: str
    expires_at: str
    allowed_confidentiality_scopes: List[str]
    capabilities: List[str]
    namespaces: List[str]


class HandoffResumeRequest(BaseModel):
    user_id: str = Field(default="default")
    agent_id: Optional[str] = Field(default=None)
    requester_agent_id: Optional[str] = Field(default=None)
    repo_path: Optional[str] = Field(default=None)
    branch: Optional[str] = Field(default=None)
    lane_type: str = Field(default="general")
    objective: Optional[str] = Field(default=None)
    agent_role: Optional[str] = Field(default=None)
    namespace: str = Field(default="default")
    statuses: Optional[List[HandoffStatus]] = Field(default=None)
    auto_create: bool = Field(default=True)


class HandoffCheckpointRequest(BaseModel):
    user_id: str = Field(default="default")
    agent_id: str
    requester_agent_id: Optional[str] = Field(default=None)
    repo_path: Optional[str] = Field(default=None)
    branch: Optional[str] = Field(default=None)
    lane_id: Optional[str] = Field(default=None)
    lane_type: str = Field(default="general")
    objective: Optional[str] = Field(default=None)
    agent_role: Optional[str] = Field(default=None)
    namespace: str = Field(default="default")
    confidentiality_scope: str = Field(default="work")
    event_type: str = Field(default="tool_complete")
    status: HandoffStatus = Field(default="active")
    task_summary: Optional[str] = Field(default=None)
    decisions_made: List[str] = Field(default_factory=list)
    files_touched: List[str] = Field(default_factory=list)
    todos_remaining: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    key_commands: List[str] = Field(default_factory=list)
    test_results: List[str] = Field(default_factory=list)
    context_snapshot: Optional[str] = Field(default=None)
    expected_version: Optional[int] = Field(default=None)


class HandoffSessionDigestRequest(BaseModel):
    user_id: str = Field(default="default")
    agent_id: str = Field(default="claude-code")
    requester_agent_id: Optional[str] = Field(default=None)
    task_summary: str
    repo: Optional[str] = Field(default=None)
    branch: Optional[str] = Field(default=None)
    lane_id: Optional[str] = Field(default=None)
    lane_type: Optional[str] = Field(default=None)
    agent_role: Optional[str] = Field(default=None)
    namespace: str = Field(default="default")
    confidentiality_scope: str = Field(default="work")
    status: HandoffStatus = Field(default="paused")
    decisions_made: List[str] = Field(default_factory=list)
    files_touched: List[str] = Field(default_factory=list)
    todos_remaining: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    key_commands: List[str] = Field(default_factory=list)
    test_results: List[str] = Field(default_factory=list)
    context_snapshot: Optional[str] = Field(default=None)
    started_at: Optional[str] = Field(default=None)
    ended_at: Optional[str] = Field(default=None)


class SearchRequestV2(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    user_id: str = Field(default="default")
    agent_id: Optional[str] = Field(default=None)
    limit: int = Field(default=10, ge=1, le=100)
    categories: Optional[List[str]] = Field(default=None)


class AddMemoryRequestV2(BaseModel):
    content: Optional[str] = Field(default=None, max_length=100000)
    messages: Optional[Union[str, List[Dict[str, Any]]]] = Field(default=None)
    user_id: str = Field(default="default")
    agent_id: Optional[str] = Field(default=None)
    metadata: Optional[Dict[str, Any]] = Field(default=None)
    categories: Optional[List[str]] = Field(default=None)
    scope: Optional[str] = Field(default="work")
    namespace: Optional[str] = Field(default="default")
    mode: Literal["staging", "direct"] = Field(default="staging", description="staging|direct")
    infer: bool = Field(default=False)
    source_app: Optional[str] = Field(default=None)
    source_type: str = Field(default="rest")
    source_event_id: Optional[str] = Field(default=None)


class SceneSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    user_id: str = Field(default="default")
    agent_id: Optional[str] = Field(default=None)
    limit: int = Field(default=10, ge=1, le=100)


class CommitResolutionRequest(BaseModel):
    reason: Optional[str] = Field(default=None)


class ConflictResolutionRequest(BaseModel):
    resolution: str = Field(description="UNRESOLVED|KEEP_EXISTING|ACCEPT_PROPOSED|KEEP_BOTH")


class DailyDigestResponse(BaseModel):
    date: str
    user_id: str
    top_conflicts: List[Dict[str, Any]]
    top_proposed_consolidations: List[Dict[str, Any]]
    scene_highlights: List[Dict[str, Any]] = Field(default_factory=list)


class SleepRunRequest(BaseModel):
    user_id: Optional[str] = Field(default=None)
    date: Optional[str] = Field(default=None)
    apply_decay: bool = Field(default=True)
    cleanup_stale_refs: bool = Field(default=True)


class NamespaceDeclareRequest(BaseModel):
    user_id: str = Field(default="default")
    namespace: str
    description: Optional[str] = Field(default=None)


class NamespacePermissionRequest(BaseModel):
    user_id: str = Field(default="default")
    namespace: str
    agent_id: str
    capability: str = Field(default="read")
    expires_at: Optional[str] = Field(default=None)


class AgentPolicyUpsertRequest(BaseModel):
    user_id: str = Field(default="default")
    agent_id: str
    allowed_confidentiality_scopes: List[str] = Field(
        default_factory=lambda: list(ALL_CONFIDENTIALITY_SCOPES)
    )
    allowed_capabilities: List[str] = Field(default_factory=lambda: list(DEFAULT_CAPABILITIES))
    allowed_namespaces: List[str] = Field(default_factory=lambda: ["default"])
