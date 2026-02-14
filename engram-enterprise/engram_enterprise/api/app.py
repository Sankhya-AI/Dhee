"""Engram v2 REST API application."""

from __future__ import annotations

import logging
import os
import threading
from datetime import date
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engram import Memory
from engram_enterprise.api.auth import (
    enforce_session_issuer,
    get_token_from_request,
    is_trusted_direct_client,
    require_session_error,
    require_token_for_untrusted_request,
)
from engram_enterprise.api.schemas import (
    AddMemoryRequestV2,
    AgentPolicyUpsertRequest,
    CommitResolutionRequest,
    ConflictResolutionRequest,
    DailyDigestResponse,
    HandoffStatus,
    HandoffCheckpointRequest,
    HandoffResumeRequest,
    HandoffSessionDigestRequest,
    NamespaceDeclareRequest,
    NamespacePermissionRequest,
    SceneSearchRequest,
    SearchRequestV2,
    SleepRunRequest,
    SessionCreateRequest,
    SessionCreateResponse,
)
from engram_enterprise.policy import feature_enabled
from engram.exceptions import FadeMemValidationError
from engram.observability import add_metrics_routes, logger as structured_logger, metrics

logger = logging.getLogger(__name__)


# Legacy response models
class SearchResultResponse(BaseModel):
    results: List[Dict[str, Any]]
    count: int
    context_packet: Optional[Dict[str, Any]] = None
    retrieval_trace: Optional[Dict[str, Any]] = None


class StatsResponse(BaseModel):
    total_memories: int
    sml_count: int
    lml_count: int
    categories: Dict[str, int]
    storage_mb: Optional[float] = None


class DecayRequest(BaseModel):
    user_id: Optional[str] = Field(default=None)
    agent_id: Optional[str] = Field(default=None)
    dry_run: bool = Field(default=False)


class DecayResponse(BaseModel):
    decayed: int
    forgotten: int
    promoted: int
    stale_refs_removed: int = 0
    dry_run: bool


app = FastAPI(
    title="Engram API",
    description="Engram v2 Personal Memory Kernel API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

_cors_origins_raw = os.environ.get("ENGRAM_CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
    if _cors_origins_raw
    else ["http://localhost:3000", "http://127.0.0.1:3000"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
add_metrics_routes(app)

_memory: Optional[Memory] = None
_memory_lock = threading.Lock()


def get_memory() -> Memory:
    global _memory
    if _memory is None:
        with _memory_lock:
            if _memory is None:
                _memory = Memory()
    return _memory


def get_kernel():
    return get_memory().kernel


def _extract_content(messages: Optional[Union[str, List[Dict[str, Any]]]], content: Optional[str]) -> str:
    if content is not None:
        return str(content)
    if isinstance(messages, str):
        return messages
    if isinstance(messages, list):
        parts = []
        for msg in messages:
            text = msg.get("content")
            if text:
                parts.append(str(text))
        return "\n".join(parts)
    return ""


_TOKEN_EXEMPT_PATHS = {
    "/health",
    "/v1/version",
    "/v1/sessions",
    "/docs",
    "/redoc",
    "/openapi.json",
}


@app.middleware("http")
async def enforce_capability_token_for_untrusted_clients(request: Request, call_next):
    if request.method.upper() == "OPTIONS":
        return await call_next(request)

    path = request.url.path.rstrip("/") or "/"
    if path.startswith("/static") or path == "/dashboard":
        return await call_next(request)

    if path.startswith("/v1") and path not in _TOKEN_EXEMPT_PATHS:
        token = get_token_from_request(request)
        try:
            require_token_for_untrusted_request(request, token)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return await call_next(request)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "engram"}


@app.get("/v1/version")
async def get_version():
    from engram import __version__

    return {"version": __version__, "api_version": "v1", "pmk_version": "2.0"}


@app.post("/v1/sessions", response_model=SessionCreateResponse)
@app.post("/v1/sessions/", response_model=SessionCreateResponse)
async def create_session(request: SessionCreateRequest, http_request: Request):
    enforce_session_issuer(http_request)
    kernel = get_kernel()
    try:
        payload = kernel.create_session(
            user_id=request.user_id,
            agent_id=request.agent_id,
            allowed_confidentiality_scopes=request.allowed_confidentiality_scopes,
            capabilities=request.capabilities,
            namespaces=request.namespaces,
            ttl_minutes=request.ttl_minutes,
        )
        return SessionCreateResponse(**payload)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.post("/v1/handoff/resume")
@app.post("/v1/handoff/resume/")
async def handoff_resume(request: HandoffResumeRequest, http_request: Request):
    token = get_token_from_request(http_request)
    kernel = get_kernel()
    try:
        return kernel.auto_resume_context(
            user_id=request.user_id,
            agent_id=request.agent_id,
            repo_path=request.repo_path,
            branch=request.branch,
            lane_type=request.lane_type,
            objective=request.objective,
            agent_role=request.agent_role,
            namespace=request.namespace,
            statuses=request.statuses,
            auto_create=request.auto_create,
            token=token,
            requester_agent_id=request.requester_agent_id,
        )
    except PermissionError as exc:
        raise require_session_error(exc)


@app.post("/v1/handoff/checkpoint")
@app.post("/v1/handoff/checkpoint/")
async def handoff_checkpoint(request: HandoffCheckpointRequest, http_request: Request):
    token = get_token_from_request(http_request)
    kernel = get_kernel()
    payload = {
        "status": request.status,
        "task_summary": request.task_summary,
        "decisions_made": request.decisions_made,
        "files_touched": request.files_touched,
        "todos_remaining": request.todos_remaining,
        "blockers": request.blockers,
        "key_commands": request.key_commands,
        "test_results": request.test_results,
        "context_snapshot": request.context_snapshot,
    }
    try:
        return kernel.auto_checkpoint(
            user_id=request.user_id,
            agent_id=request.agent_id,
            payload=payload,
            event_type=request.event_type,
            repo_path=request.repo_path,
            branch=request.branch,
            lane_id=request.lane_id,
            lane_type=request.lane_type,
            objective=request.objective,
            agent_role=request.agent_role,
            namespace=request.namespace,
            confidentiality_scope=request.confidentiality_scope,
            expected_version=request.expected_version,
            token=token,
            requester_agent_id=request.requester_agent_id,
        )
    except PermissionError as exc:
        raise require_session_error(exc)


@app.get("/v1/handoff/lanes")
@app.get("/v1/handoff/lanes/")
async def list_handoff_lanes(
    http_request: Request,
    user_id: str = Query(default="default"),
    repo_path: Optional[str] = Query(default=None),
    status: Optional[HandoffStatus] = Query(default=None),
    statuses: Optional[List[HandoffStatus]] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    requester_agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    kernel = get_kernel()
    try:
        lanes = kernel.list_handoff_lanes(
            user_id=user_id,
            repo_path=repo_path,
            status=status,
            statuses=statuses,
            limit=limit,
            token=token,
            requester_agent_id=requester_agent_id,
        )
        return {"lanes": lanes, "count": len(lanes)}
    except PermissionError as exc:
        raise require_session_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/handoff/sessions/digest")
@app.post("/v1/handoff/sessions/digest/")
async def save_handoff_session_digest(request: HandoffSessionDigestRequest, http_request: Request):
    token = get_token_from_request(http_request)
    kernel = get_kernel()
    digest = {
        "task_summary": request.task_summary,
        "repo": request.repo,
        "branch": request.branch,
        "lane_id": request.lane_id,
        "lane_type": request.lane_type,
        "agent_role": request.agent_role,
        "namespace": request.namespace,
        "confidentiality_scope": request.confidentiality_scope,
        "status": request.status,
        "decisions_made": request.decisions_made,
        "files_touched": request.files_touched,
        "todos_remaining": request.todos_remaining,
        "blockers": request.blockers,
        "key_commands": request.key_commands,
        "test_results": request.test_results,
        "context_snapshot": request.context_snapshot,
        "started_at": request.started_at,
        "ended_at": request.ended_at,
    }
    try:
        return kernel.save_session_digest(
            user_id=request.user_id,
            agent_id=request.agent_id,
            digest=digest,
            token=token,
            requester_agent_id=request.requester_agent_id or request.agent_id,
        )
    except PermissionError as exc:
        raise require_session_error(exc)


@app.get("/v1/handoff/sessions/last")
@app.get("/v1/handoff/sessions/last/")
async def get_handoff_last_session(
    http_request: Request,
    user_id: str = Query(default="default"),
    agent_id: Optional[str] = Query(default=None),
    requester_agent_id: Optional[str] = Query(default=None),
    repo: Optional[str] = Query(default=None),
    statuses: Optional[List[HandoffStatus]] = Query(default=None),
):
    token = get_token_from_request(http_request)
    kernel = get_kernel()
    try:
        session = kernel.get_last_session(
            user_id=user_id,
            agent_id=agent_id,
            repo=repo,
            statuses=statuses,
            token=token,
            requester_agent_id=requester_agent_id or agent_id,
        )
        if session:
            return session
        return {"error": "No sessions found"}
    except PermissionError as exc:
        raise require_session_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/v1/handoff/sessions")
@app.get("/v1/handoff/sessions/")
async def list_handoff_sessions(
    http_request: Request,
    user_id: str = Query(default="default"),
    agent_id: Optional[str] = Query(default=None),
    requester_agent_id: Optional[str] = Query(default=None),
    repo: Optional[str] = Query(default=None),
    status: Optional[HandoffStatus] = Query(default=None),
    statuses: Optional[List[HandoffStatus]] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
):
    token = get_token_from_request(http_request)
    kernel = get_kernel()
    try:
        sessions = kernel.list_sessions(
            user_id=user_id,
            agent_id=agent_id,
            repo=repo,
            status=status,
            statuses=statuses,
            limit=limit,
            token=token,
            requester_agent_id=requester_agent_id or agent_id,
        )
        return {"sessions": sessions, "count": len(sessions)}
    except PermissionError as exc:
        raise require_session_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/search", response_model=SearchResultResponse)
@app.post("/v1/search/", response_model=SearchResultResponse)
@app.post("/v1/memories/search", response_model=SearchResultResponse)
@app.post("/v1/memories/search/", response_model=SearchResultResponse)
async def search_memories(request: SearchRequestV2, http_request: Request):
    with metrics.measure("api_search", user_id=request.user_id):
        token = get_token_from_request(http_request)
        kernel = get_kernel()
        try:
            payload = kernel.search(
                query=request.query,
                user_id=request.user_id,
                agent_id=request.agent_id,
                token=token,
                limit=request.limit,
                categories=request.categories,
            )
            results = payload.get("results", [])
            metrics.record_search(0, results_count=len(results))
            return SearchResultResponse(
                results=results,
                count=len(results),
                context_packet=payload.get("context_packet"),
                retrieval_trace=payload.get("retrieval_trace"),
            )
        except PermissionError as exc:
            raise require_session_error(exc)
        except Exception as exc:
            logger.exception("Error searching memories")
            raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/v1/scenes")
@app.get("/v1/scenes/")
async def list_scenes(
    user_id: Optional[str] = Query(default=None),
    topic: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    return {"scenes": get_memory().get_scenes(user_id=user_id, topic=topic, limit=limit)}


@app.post("/v1/scenes/search")
@app.post("/v1/scenes/search/")
async def search_scenes(request: SceneSearchRequest, http_request: Request):
    token = get_token_from_request(http_request)
    kernel = get_kernel()
    try:
        return kernel.search_scenes(
            query=request.query,
            user_id=request.user_id,
            agent_id=request.agent_id,
            token=token,
            limit=request.limit,
        )
    except PermissionError as exc:
        raise require_session_error(exc)


@app.get("/v1/scenes/{scene_id}")
@app.get("/v1/scenes/{scene_id}/")
async def get_scene(
    scene_id: str,
    http_request: Request,
    user_id: str = Query(default="default"),
    agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    kernel = get_kernel()
    try:
        scene = kernel.get_scene(
            scene_id=scene_id,
            user_id=user_id,
            agent_id=agent_id,
            token=token,
        )
    except PermissionError as exc:
        raise require_session_error(exc)

    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    return scene


@app.post("/v1/memories", response_model=Dict[str, Any])
@app.post("/v1/memories/", response_model=Dict[str, Any])
async def add_memory(request: AddMemoryRequestV2, http_request: Request):
    token = get_token_from_request(http_request)
    kernel = get_kernel()
    content = _extract_content(request.messages, request.content)
    if not content:
        raise HTTPException(status_code=400, detail="content or messages is required")

    mode = (request.mode or "staging").lower()
    if mode not in {"staging", "direct"}:
        raise HTTPException(status_code=400, detail="mode must be 'staging' or 'direct'")

    trusted_direct = mode == "direct" and is_trusted_direct_client(http_request)

    try:
        return kernel.propose_write(
            content=content,
            token=token,
            user_id=request.user_id,
            agent_id=request.agent_id,
            categories=request.categories,
            metadata=request.metadata,
            scope=request.scope or "work",
            namespace=request.namespace or "default",
            mode=mode,
            infer=request.infer,
            source_app=request.source_app,
            trusted_direct=trusted_direct,
            source_type=request.source_type,
            source_event_id=request.source_event_id,
        )
    except PermissionError as exc:
        raise require_session_error(exc)
    except Exception as exc:
        logger.exception("Error creating proposal/direct memory")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/v1/staging/commits")
@app.get("/v1/staging/commits/")
async def list_staging_commits(
    http_request: Request,
    user_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    kernel = get_kernel()
    token = get_token_from_request(http_request)
    try:
        return kernel.list_pending_commits(
            user_id=user_id,
            agent_id=agent_id,
            token=token,
            status=status,
            limit=limit,
        )
    except PermissionError as exc:
        raise require_session_error(exc)


@app.post("/v1/staging/commits/{commit_id}/approve")
async def approve_commit(
    commit_id: str,
    http_request: Request,
    agent_id: Optional[str] = Query(default=None),
):
    kernel = get_kernel()
    token = get_token_from_request(http_request)
    try:
        return kernel.approve_commit(commit_id=commit_id, token=token, agent_id=agent_id)
    except PermissionError as exc:
        raise require_session_error(exc)


@app.post("/v1/staging/commits/{commit_id}/reject")
async def reject_commit(
    commit_id: str,
    request: CommitResolutionRequest,
    http_request: Request,
    agent_id: Optional[str] = Query(default=None),
):
    kernel = get_kernel()
    token = get_token_from_request(http_request)
    try:
        return kernel.reject_commit(commit_id=commit_id, reason=request.reason, token=token, agent_id=agent_id)
    except PermissionError as exc:
        raise require_session_error(exc)


@app.post("/v1/conflicts/{stash_id}/resolve")
async def resolve_conflict(
    stash_id: str,
    request: ConflictResolutionRequest,
    http_request: Request,
    agent_id: Optional[str] = Query(default=None),
):
    kernel = get_kernel()
    token = get_token_from_request(http_request)
    try:
        return kernel.resolve_conflict(stash_id=stash_id, resolution=request.resolution, token=token, agent_id=agent_id)
    except PermissionError as exc:
        raise require_session_error(exc)


@app.get("/v1/digest/daily", response_model=DailyDigestResponse)
async def get_daily_digest(
    http_request: Request,
    user_id: str = Query(default="default"),
    agent_id: Optional[str] = Query(default=None),
    date_value: Optional[str] = Query(default=None, alias="date"),
):
    kernel = get_kernel()
    digest_date = date_value or date.today().isoformat()
    token = get_token_from_request(http_request)
    try:
        payload = kernel.get_daily_digest(user_id=user_id, date_str=digest_date, token=token, agent_id=agent_id)
        return DailyDigestResponse(**payload)
    except PermissionError as exc:
        raise require_session_error(exc)


@app.get("/v1/trust")
async def get_agent_trust(
    http_request: Request,
    user_id: str = Query(default="default"),
    agent_id: str = Query(...),
    requester_agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    try:
        return get_kernel().get_agent_trust(
            user_id=user_id,
            agent_id=agent_id,
            token=token,
            requester_agent_id=requester_agent_id,
        )
    except PermissionError as exc:
        raise require_session_error(exc)


@app.get("/v1/namespaces")
async def list_namespaces(
    http_request: Request,
    user_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    try:
        namespaces = get_kernel().list_namespaces(user_id=user_id, token=token, agent_id=agent_id)
    except PermissionError as exc:
        raise require_session_error(exc)
    return {"namespaces": namespaces, "count": len(namespaces)}


@app.post("/v1/namespaces")
async def declare_namespace(
    request: NamespaceDeclareRequest,
    http_request: Request,
    agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    try:
        payload = get_kernel().declare_namespace(
            user_id=request.user_id,
            namespace=request.namespace,
            description=request.description,
            token=token,
            agent_id=agent_id,
        )
        return payload
    except PermissionError as exc:
        raise require_session_error(exc)


@app.post("/v1/namespaces/permissions")
async def grant_namespace_permission(
    request: NamespacePermissionRequest,
    http_request: Request,
    requester_agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    try:
        payload = get_kernel().grant_namespace_permission(
            user_id=request.user_id,
            namespace=request.namespace,
            agent_id=request.agent_id,
            capability=request.capability,
            expires_at=request.expires_at,
            token=token,
            requester_agent_id=requester_agent_id,
        )
        return payload
    except PermissionError as exc:
        raise require_session_error(exc)


@app.post("/v1/agent-policies")
async def upsert_agent_policy(
    request: AgentPolicyUpsertRequest,
    http_request: Request,
    requester_agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    try:
        payload = get_kernel().upsert_agent_policy(
            user_id=request.user_id,
            agent_id=request.agent_id,
            allowed_confidentiality_scopes=request.allowed_confidentiality_scopes,
            allowed_capabilities=request.allowed_capabilities,
            allowed_namespaces=request.allowed_namespaces,
            token=token,
            requester_agent_id=requester_agent_id,
        )
        return payload
    except PermissionError as exc:
        raise require_session_error(exc)


@app.get("/v1/agent-policies")
async def list_agent_policies(
    http_request: Request,
    user_id: str = Query(default="default"),
    agent_id: Optional[str] = Query(default=None),
    include_wildcard: bool = Query(default=True),
    requester_agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    try:
        kernel = get_kernel()
        if agent_id:
            policy = kernel.get_agent_policy(
                user_id=user_id,
                agent_id=agent_id,
                include_wildcard=include_wildcard,
                token=token,
                requester_agent_id=requester_agent_id,
            )
            return {"policy": policy}
        policies = kernel.list_agent_policies(
            user_id=user_id,
            token=token,
            requester_agent_id=requester_agent_id,
        )
        return {"policies": policies, "count": len(policies)}
    except PermissionError as exc:
        raise require_session_error(exc)


@app.delete("/v1/agent-policies")
async def delete_agent_policy(
    http_request: Request,
    user_id: str = Query(default="default"),
    agent_id: str = Query(...),
    requester_agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    try:
        return get_kernel().delete_agent_policy(
            user_id=user_id,
            agent_id=agent_id,
            token=token,
            requester_agent_id=requester_agent_id,
        )
    except PermissionError as exc:
        raise require_session_error(exc)


@app.post("/v1/sleep/run")
async def run_sleep_cycle(
    request: SleepRunRequest,
    http_request: Request,
    agent_id: Optional[str] = Query(default=None),
):
    token = get_token_from_request(http_request)
    try:
        payload = get_memory().run_sleep_cycle(
            user_id=request.user_id,
            date_str=request.date,
            apply_decay=request.apply_decay,
            cleanup_stale_refs=request.cleanup_stale_refs,
            token=token,
            agent_id=agent_id,
        )
        return payload
    except PermissionError as exc:
        raise require_session_error(exc)


# ---------------------------------------------------------------------------
# Legacy compatibility endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/memories", response_model=Dict[str, Any])
@app.get("/v1/memories/", response_model=Dict[str, Any])
async def list_memories(
    user_id: str = Query(default="default"),
    agent_id: Optional[str] = Query(default=None),
    layer: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    memory = get_memory()
    payload = memory.get_all(user_id=user_id, agent_id=agent_id, layer=layer, limit=limit)
    memories = payload.get("results", payload) if isinstance(payload, dict) else payload
    return {"memories": memories, "count": len(memories)}


@app.get("/v1/memories/{memory_id}", response_model=Dict[str, Any])
@app.get("/v1/memories/{memory_id}/", response_model=Dict[str, Any])
async def get_memory_by_id(memory_id: str):
    memory = get_memory()
    result = memory.get(memory_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return result


@app.put("/v1/memories/{memory_id}", response_model=Dict[str, Any])
@app.put("/v1/memories/{memory_id}/", response_model=Dict[str, Any])
async def update_memory(memory_id: str, request: Dict[str, Any], http_request: Request):
    token = get_token_from_request(http_request)
    require_token_for_untrusted_request(http_request, token)
    memory = get_memory()
    result = memory.update(memory_id, request)
    return result


@app.delete("/v1/memories/{memory_id}")
@app.delete("/v1/memories/{memory_id}/")
async def delete_memory(memory_id: str, http_request: Request):
    token = get_token_from_request(http_request)
    require_token_for_untrusted_request(http_request, token)
    memory = get_memory()
    memory.delete(memory_id)
    return {"status": "deleted", "id": memory_id}


@app.delete("/v1/memories", response_model=Dict[str, Any])
@app.delete("/v1/memories/", response_model=Dict[str, Any])
async def delete_memories(
    http_request: Request,
    user_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
    app_id: Optional[str] = Query(default=None),
    dry_run: bool = Query(default=False, description="Preview what would be deleted without actually deleting"),
):
    token = get_token_from_request(http_request)
    require_token_for_untrusted_request(http_request, token)
    memory = get_memory()
    try:
        return memory.delete_all(user_id=user_id, agent_id=agent_id, run_id=run_id, app_id=app_id, dry_run=dry_run)
    except FadeMemValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message)


@app.get("/v1/memories/{memory_id}/history", response_model=List[Dict[str, Any]])
@app.get("/v1/memories/{memory_id}/history/", response_model=List[Dict[str, Any]])
async def get_memory_history(memory_id: str):
    return get_memory().history(memory_id)


@app.post("/v1/decay", response_model=DecayResponse)
async def apply_decay(request: DecayRequest):
    memory = get_memory()
    if request.dry_run:
        return DecayResponse(decayed=0, forgotten=0, promoted=0, stale_refs_removed=0, dry_run=True)
    result = memory.apply_decay(scope={"user_id": request.user_id, "agent_id": request.agent_id})
    return DecayResponse(
        decayed=result.get("decayed", 0),
        forgotten=result.get("forgotten", 0),
        promoted=result.get("promoted", 0),
        stale_refs_removed=result.get("stale_refs_removed", 0),
        dry_run=False,
    )


@app.get("/v1/stats", response_model=StatsResponse)
async def get_stats(
    user_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
):
    stats = get_memory().get_stats(user_id=user_id, agent_id=agent_id)
    return StatsResponse(
        total_memories=stats.get("total", 0),
        sml_count=stats.get("sml_count", 0),
        lml_count=stats.get("lml_count", 0),
        categories=stats.get("categories", {}),
        storage_mb=stats.get("storage_mb"),
    )


@app.get("/v1/categories")
async def list_categories():
    return {"categories": get_memory().get_categories()}


@app.get("/v1/categories/tree")
async def get_category_tree():
    return {"tree": get_memory().get_category_tree()}


@app.get("/v1/categories/{category_id}/summary")
async def get_category_summary(category_id: str, regenerate: bool = Query(default=False)):
    summary = get_memory().get_category_summary(category_id, regenerate=regenerate)
    return {"category_id": category_id, "summary": summary}


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/conflicts")
@app.get("/v1/conflicts/")
async def list_conflicts(
    user_id: Optional[str] = Query(default=None),
    resolution: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    return {"conflicts": get_memory().db.list_conflict_stash(
        user_id=user_id, resolution=resolution, limit=limit,
    )}


@app.post("/v1/memories/{memory_id}/promote")
async def promote_memory(memory_id: str):
    result = get_memory().promote(memory_id)
    return {"status": "promoted", "id": memory_id, **result}


@app.post("/v1/memories/{memory_id}/demote")
async def demote_memory(memory_id: str):
    result = get_memory().demote(memory_id)
    return {"status": "demoted", "id": memory_id, **result}


@app.get("/v1/profiles")
@app.get("/v1/profiles/")
async def list_profiles(
    user_id: Optional[str] = Query(default=None),
):
    return {"profiles": get_memory().get_all_profiles(user_id=user_id)}


@app.get("/v1/dashboard/constellation")
async def get_constellation(
    user_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    return get_memory().get_constellation_data(user_id=user_id, limit=limit)


@app.get("/v1/decay-log")
async def get_decay_log(
    limit: int = Query(default=20, ge=1, le=100),
):
    return {"entries": get_memory().get_decay_log(limit=limit)}


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/dashboard")
async def serve_dashboard():
    html_path = os.path.join(_STATIC_DIR, "dashboard.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return FileResponse(html_path, media_type="text/html")


# Mount static files last so it doesn't shadow API routes
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


_sleep_cycle_thread: Optional[threading.Thread] = None
_sleep_cycle_stop = threading.Event()


def _sleep_cycle_worker() -> None:
    interval_minutes_raw = os.environ.get("ENGRAM_V2_SLEEP_CYCLE_INTERVAL_MINUTES", "60")
    try:
        interval_minutes = max(5, int(interval_minutes_raw))
    except Exception:
        interval_minutes = 60
    while not _sleep_cycle_stop.is_set():
        try:
            get_memory().run_sleep_cycle(
                user_id=None,
                date_str=None,
                apply_decay=feature_enabled("ENGRAM_V2_SLEEP_CYCLE_APPLY_DECAY", default=True),
                cleanup_stale_refs=feature_enabled("ENGRAM_V2_SLEEP_CYCLE_REF_GC", default=True),
            )
        except Exception:
            logger.exception("Sleep cycle background run failed")
        if _sleep_cycle_stop.wait(interval_minutes * 60):
            break


@app.on_event("startup")
async def startup_events():
    global _sleep_cycle_thread
    if not feature_enabled("ENGRAM_V2_SLEEP_CYCLE_ENABLED", default=False):
        return
    if _sleep_cycle_thread and _sleep_cycle_thread.is_alive():
        return
    _sleep_cycle_stop.clear()
    _sleep_cycle_thread = threading.Thread(target=_sleep_cycle_worker, daemon=True, name="engram-sleep-cycle")
    _sleep_cycle_thread.start()
    logger.info("Started sleep-cycle background worker")


@app.on_event("shutdown")
async def shutdown_events():
    if _sleep_cycle_thread and _sleep_cycle_thread.is_alive():
        _sleep_cycle_stop.set()
        _sleep_cycle_thread.join(timeout=2)
