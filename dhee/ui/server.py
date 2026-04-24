"""FastAPI bridge between the Sankhya SPA and the Dhee substrate.

Endpoints map the fields the prototype UI expects onto real Dhee state:

- /api/memories           FullMemory.get_all -> engram shape
- /api/memories (POST)    remember()
- /api/memories/{id}      DELETE archive
- /api/router/stats       router.stats.compute_stats
- /api/router/policy      router.policy.load + router.tune.build_report
- /api/router/tune (POST) router.tune.apply
- /api/meta-buddhi        MetaBuddhi snapshot
- /api/evolution          samskara / evolution log
- /api/conflicts          derived from memory history
- /api/tasks              shared_tasks.shared_task_snapshot
- /api/status             overall health
- /api/security/api-keys  encrypted API-key storage + rotation

Honesty: where a piece of the prototype has no live adapter yet (e.g.
the curated evolution timeline with pretty labels), we synthesize a
minimal shape from real session logs and mark `live: false` on that
endpoint so the UI can show a "derived" badge. No silent mocks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger(__name__)


class CaptureSessionStartPayload(BaseModel):
    source_app: str
    namespace: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class CaptureSessionEndPayload(BaseModel):
    session_id: Optional[str] = None
    distill: bool = True
    summary_hint: Optional[str] = None


class CaptureActionPayload(BaseModel):
    session_id: str
    source_app: Optional[str] = None
    namespace: Optional[str] = None
    created_at: Optional[str] = None
    action_type: str
    target: Optional[Dict[str, Any]] = None
    surface: Optional[Dict[str, Any]] = None
    surface_id: Optional[str] = None
    surface_type: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    path: Optional[str] = None
    path_hint: Optional[List[str]] = None
    previous_surface_id: Optional[str] = None
    capture_mode: Optional[str] = None
    confidence: Optional[float] = None
    action_payload: Optional[Dict[str, Any]] = None
    source_kind: Optional[str] = None
    before_context: Optional[str] = None
    after_context: Optional[str] = None
    before_frame_ref: Optional[str] = None
    after_frame_ref: Optional[str] = None
    task_instruction: Optional[str] = None
    recent_actions: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class CaptureObservationPayload(BaseModel):
    session_id: str
    source_app: Optional[str] = None
    namespace: Optional[str] = None
    created_at: Optional[str] = None
    action_id: Optional[str] = None
    source_kind: Optional[str] = None
    kind: Optional[str] = None
    text: Optional[str] = None
    text_payload: Optional[str] = None
    structured: Optional[Dict[str, Any]] = None
    structured_payload: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None
    surface: Optional[Dict[str, Any]] = None


class RememberPayload(BaseModel):
    content: str
    tier: Optional[str] = None
    tags: Optional[List[str]] = None
    source: Optional[str] = None


class CaptureArtifactPayload(BaseModel):
    session_id: str
    source_app: Optional[str] = None
    namespace: Optional[str] = None
    created_at: Optional[str] = None
    action_id: Optional[str] = None
    content_base64: Optional[str] = None
    path: Optional[str] = None
    mime_type: Optional[str] = None
    artifact_type: Optional[str] = None
    retention: Optional[str] = None
    ttl_hours: Optional[int] = None
    surface: Optional[Dict[str, Any]] = None
    surface_id: Optional[str] = None
    surface_type: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    path_hint: Optional[List[str]] = None
    label: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class CapturePreferencePayload(BaseModel):
    source_app: str
    enabled: bool
    mode: str = "sampled"
    metadata: Optional[Dict[str, Any]] = None


class MemoryAskPayload(BaseModel):
    query: str
    source_app: Optional[str] = None
    limit: int = 6


class AgentContextPackPayload(BaseModel):
    task_instruction: str
    agent_id: Optional[str] = None
    source_app: Optional[str] = None
    current_frame_ref: Optional[str] = None
    current_context_text: Optional[str] = None
    recent_actions: Optional[List[str]] = None
    limit: int = 5


class WorldContextPackPayload(BaseModel):
    current_frame_ref: str
    current_context_text: str
    task_instruction: str
    recent_actions: Optional[List[str]] = None
    limit: int = 5


class WorkspaceRootCreatePayload(BaseModel):
    name: str
    description: Optional[str] = None
    root_path: Optional[str] = None


class WorkspaceRootUpdatePayload(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class WorkspaceCreatePayload(BaseModel):
    workspace_path: str
    label: Optional[str] = None
    folder_path: Optional[str] = None
    is_primary: bool = False
    folders: Optional[List[str]] = None


class WorkspaceFolderPayload(BaseModel):
    path: str
    label: Optional[str] = None


class WorkspaceUpdatePayload(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    root_path: Optional[str] = None


class WorkspaceProjectCreatePayload(BaseModel):
    name: str
    description: Optional[str] = None
    default_runtime: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    scope_rules: Optional[List[Dict[str, Any]]] = None


class WorkspaceProjectUpdatePayload(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_runtime: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    scope_rules: Optional[List[Dict[str, Any]]] = None


class FolderPickPayload(BaseModel):
    prompt: Optional[str] = None


class AssetAskPayload(BaseModel):
    question: str


class ConflictResolutionPayload(BaseModel):
    action: str
    merged_content: Optional[str] = None
    reason: Optional[str] = None


class SessionLaunchPayload(BaseModel):
    runtime: str
    title: Optional[str] = None
    permission_mode: Optional[str] = None
    task_id: Optional[str] = None
    project_id: Optional[str] = None


class WorkspaceLineMessagePayload(BaseModel):
    project_id: Optional[str] = None
    target_project_id: Optional[str] = None
    channel: Optional[str] = None
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    message_kind: str = "update"
    title: Optional[str] = None
    body: str
    metadata: Optional[Dict[str, Any]] = None


# ─── Shape helpers ────────────────────────────────────────────────────────────

_TIER_BY_SCORE = [
    (0.90, "canonical"),
    (0.70, "high"),
    (0.40, "medium"),
    (0.00, "short-term"),
]


def _tier_for(mem: Dict[str, Any]) -> str:
    meta = mem.get("metadata") or {}
    explicit = meta.get("tier") or mem.get("tier")
    if explicit in {"canonical", "high", "medium", "short-term", "avoid"}:
        return explicit
    if meta.get("avoid") or mem.get("avoid"):
        return "avoid"
    score = float(
        mem.get("score")
        or meta.get("confidence")
        or meta.get("importance")
        or 0.5
    )
    for threshold, tier in _TIER_BY_SCORE:
        if score >= threshold:
            return tier
    return "short-term"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _engram_from_memory(mem: Dict[str, Any]) -> Dict[str, Any]:
    meta = mem.get("metadata") or {}
    content = mem.get("memory") or mem.get("content") or mem.get("text") or ""
    created = (
        mem.get("created_at")
        or mem.get("createdAt")
        or meta.get("created_at")
        or ""
    )
    if isinstance(created, (int, float)):
        created = time.strftime("%Y-%m-%d", time.localtime(float(created)))
    elif isinstance(created, str) and "T" in created:
        created = created.split("T", 1)[0]
    decay = mem.get("decay")
    if decay is None:
        decay = meta.get("decay")
    if decay is None:
        # Flat 1.0 unless Dhee has populated a decay signal.
        decay = 1.0
    tags = meta.get("tags") or mem.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "id": str(mem.get("id") or meta.get("id") or meta.get("memory_id") or ""),
        "tier": _tier_for(mem),
        "content": content,
        "source": str(meta.get("source") or mem.get("source") or "dhee"),
        "created": created or time.strftime("%Y-%m-%d"),
        "tags": list(tags),
        "decay": float(decay),
        "reaffirmed": int(meta.get("reaffirmed") or mem.get("reaffirmed") or 0),
        "tokens": _estimate_tokens(content),
    }


# ─── App factory ──────────────────────────────────────────────────────────────


def create_app(*, serve_static: bool = True, dev_mode: bool = False) -> FastAPI:
    app = FastAPI(title="Sankhya — Dhee UI", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─── Memory ──────────────────────────────────────────────────────────────

    def _get_memory():
        from dhee.mcp_server import get_memory_instance

        return get_memory_instance()

    _memory_os_service = None

    def _get_memory_os_service():
        nonlocal _memory_os_service
        if _memory_os_service is None:
            from dhee.world_memory import MemoryOSService

            _memory_os_service = MemoryOSService.from_default_runtime(memory=_get_memory())
        return _memory_os_service

    @app.get("/api/memories")
    def list_memories(limit: int = 500) -> Dict[str, Any]:
        try:
            mem = _get_memory()
            raw = mem.get_all(
                user_id=os.environ.get("DHEE_UI_USER_ID", "default"),
                limit=limit,
            )
            # get_all returns {"results": [...]} in some codepaths
            if isinstance(raw, dict):
                raw = raw.get("results") or raw.get("memories") or []
            engrams = [_engram_from_memory(m) for m in raw if m]
            return {"live": True, "engrams": engrams, "count": len(engrams)}
        except Exception as exc:  # noqa: BLE001
            log.warning("list_memories failed: %s", exc)
            return {"live": False, "engrams": [], "count": 0, "error": str(exc)}


    @app.post("/api/memories")
    def remember(payload: RememberPayload) -> Dict[str, Any]:
        try:
            mem = _get_memory()
            result = mem.add(
                messages=[{"role": "user", "content": payload.content}],
                user_id=os.environ.get("DHEE_UI_USER_ID", "default"),
                metadata={
                    "source": payload.source or "sankhya-ui",
                    "tier": payload.tier or "short-term",
                    "tags": payload.tags or [],
                },
            )
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.delete("/api/memories/{memory_id}")
    def archive_memory(memory_id: str) -> Dict[str, Any]:
        try:
            mem = _get_memory()
            mem.delete(memory_id)
            return {"ok": True, "id": memory_id}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ─── Router ──────────────────────────────────────────────────────────────

    @app.get("/api/router/stats")
    def router_stats(agent_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            from dhee.router import stats as rstats

            selected_agent = None if agent_id in (None, "", "all") else agent_id
            s = rstats.compute_stats(agent_id=selected_agent).to_dict()
            agents = rstats.list_agent_stats()
            codex_native = _router_codex_native_usage(_ui_repo())
            tools = []
            for name, calls in s.get("calls_by_tool", {}).items():
                tokens_saved = int(
                    s.get("est_tokens_diverted", 0)
                    * (calls / max(1, s.get("total_calls", 1)))
                )
                tools.append(
                    {
                        "name": name,
                        "calls": calls,
                        "tokensSaved": tokens_saved,
                        "expansions": int(
                            s.get("expansion_calls", 0)
                            * (calls / max(1, s.get("total_calls", 1)))
                        ),
                        "avgDigest": 60,
                        "avgRaw": int(tokens_saved / max(1, calls)) if calls else 0,
                    }
                )
            return {
                "live": True,
                "selectedAgent": selected_agent or "all",
                "sessionTokensSaved": s.get("est_tokens_diverted", 0),
                "totalCalls": s.get("total_calls", 0),
                "expansionRate": s.get("expansion_rate", 0.0),
                "sessionCost": round(
                    s.get("est_tokens_diverted", 0) * 1e-6 * 3.0, 4
                ),
                "estimatedFullCost": round(
                    (s.get("est_tokens_diverted", 0) + s.get("bytes_stored", 0) / 3.5)
                    * 1e-6
                    * 3.0,
                    4,
                ),
                "tools": tools,
                "agents": agents,
                "sessions": s.get("sessions", 0),
                "bytesStored": s.get("bytes_stored", 0),
                "dailySavings": _seven_day_savings(selected_agent),
                "days": _seven_day_labels(),
                "codexNative": codex_native,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("router_stats failed: %s", exc)
            return {"live": False, "error": str(exc)}

    @app.get("/api/router/policy")
    def router_policy() -> Dict[str, Any]:
        try:
            from dhee.router import policy, tune

            data = policy.load()
            report = tune.build_report()
            prev = {
                (s.tool, s.intent): s.current_depth for s in getattr(report, "suggestions", [])
            }
            depths_map = data.get("depths", {})
            depth_rank = {"shallow": 1, "normal": 2, "deep": 3}
            policies = []
            for tool, intents in depths_map.items():
                for intent, depth in intents.items():
                    expansion = 0.0
                    for s in getattr(report, "suggestions", []):
                        if s.tool == tool and s.intent == intent:
                            expansion = getattr(s, "expansion_rate", 0.0)
                    prev_depth = prev.get((tool, intent), depth)
                    policies.append(
                        {
                            "intent": intent,
                            "label": intent.replace("_", " ").title(),
                            "depth": depth_rank.get(depth, 2),
                            "prevDepth": depth_rank.get(prev_depth, 2),
                            "expansionRate": expansion,
                            "tuned": depth != prev_depth,
                            "tool": tool,
                        }
                    )
            return {"live": True, "policies": policies, "raw": data}
        except Exception as exc:  # noqa: BLE001
            log.warning("router_policy failed: %s", exc)
            return {"live": False, "policies": [], "error": str(exc)}

    @app.post("/api/router/tune")
    def router_tune_apply() -> Dict[str, Any]:
        try:
            from dhee.router import tune

            report = tune.build_report()
            applied = tune.apply(report)
            return {
                "ok": True,
                "applied": applied,
                "human": tune.format_human(report),
                "suggestions": [
                    {
                        "tool": s.tool,
                        "intent": s.intent,
                        "from": s.current_depth,
                        "to": s.proposed_depth,
                        "reason": getattr(s, "reason", ""),
                    }
                    for s in getattr(report, "suggestions", [])
                ],
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ─── MetaBuddhi + Evolution ──────────────────────────────────────────────

    @app.get("/api/meta-buddhi")
    def meta_buddhi_status() -> Dict[str, Any]:
        try:
            from dhee.core import meta_buddhi as mb

            snapshot: Dict[str, Any] = {
                "status": "active",
                "strategy": "Adaptive depth v2.3",
            }
            if hasattr(mb, "latest_snapshot"):
                snapshot.update(mb.latest_snapshot() or {})  # type: ignore[attr-defined]
            snapshot.setdefault("sessionInsights", 0)
            snapshot.setdefault("totalInsights", 0)
            snapshot.setdefault("pendingProposals", 0)
            snapshot.setdefault("lastGate", "")
            snapshot.setdefault("confidenceGroups", _default_confidence_groups())
            return {"live": True, **snapshot}
        except Exception as exc:  # noqa: BLE001
            log.warning("meta_buddhi failed: %s", exc)
            return {
                "live": False,
                "status": "unknown",
                "strategy": "—",
                "sessionInsights": 0,
                "totalInsights": 0,
                "pendingProposals": 0,
                "lastGate": "",
                "confidenceGroups": _default_confidence_groups(),
                "error": str(exc),
            }

    @app.get("/api/evolution")
    def evolution_timeline() -> Dict[str, Any]:
        events = _load_evolution_events()
        return {"live": bool(events), "events": events}

    # ─── Conflicts ───────────────────────────────────────────────────────────

    @app.get("/api/conflicts")
    def conflicts() -> Dict[str, Any]:
        try:
            from dhee.mcp_server import get_memory_instance

            mem = get_memory_instance()
            items: List[Dict[str, Any]] = []
            supported = bool(hasattr(mem, "resolve_conflict"))
            if hasattr(mem, "get_conflicts"):
                items = list(mem.get_conflicts())  # type: ignore[attr-defined]
            return {
                "live": True,
                "supported": supported,
                "conflicts": items,
                "resolutionMode": "native" if supported else "read-only",
            }
        except Exception as exc:  # noqa: BLE001
            log.info("conflicts: no live adapter (%s)", exc)
            return {
                "live": False,
                "supported": False,
                "conflicts": [],
                "resolutionMode": "unavailable",
            }

    @app.post("/api/conflicts/{conflict_id}/resolve")
    def resolve_conflict(
        conflict_id: str,
        payload: ConflictResolutionPayload = Body(...),
    ) -> Dict[str, Any]:
        try:
            from dhee.mcp_server import get_memory_instance

            mem = get_memory_instance()
            if not hasattr(mem, "resolve_conflict"):
                raise HTTPException(
                    status_code=501,
                    detail="Conflict resolution is not available in this Dhee runtime yet",
                )
            result = mem.resolve_conflict(  # type: ignore[attr-defined]
                conflict_id,
                payload.action,
                merged_content=payload.merged_content,
                reason=payload.reason,
            )
            return {"ok": True, "id": conflict_id, "action": payload.action, "result": result}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ─── Projects / Workspaces / Sessions ───────────────────────────────────

    @app.get("/api/projects")
    def list_projects_api() -> Dict[str, Any]:
        try:
            return _build_project_index_payload()
        except Exception as exc:  # noqa: BLE001
            log.warning("projects failed: %s", exc)
            return {
                "live": False,
                "workspaces": [],
                "currentProjectId": "",
                "currentWorkspaceId": "",
                "currentSessionId": "",
                "error": str(exc),
            }

    @app.get("/api/workspaces")
    def list_workspaces_api() -> Dict[str, Any]:
        try:
            return _build_project_index_payload()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/workspaces")
    def create_workspace_api(payload: WorkspaceRootCreatePayload) -> Dict[str, Any]:
        try:
            db = _get_db()
            root_path = os.path.abspath(os.path.expanduser(payload.root_path or _ui_repo()))
            workspace = db.upsert_workspace(
                {
                    "user_id": _ui_user_id(),
                    "name": payload.name,
                    "description": payload.description,
                    "root_path": root_path,
                    "metadata": {"created_via": "sankhya-ui"},
                }
            )
            db.upsert_workspace_mount(
                {
                    "workspace_id": workspace["id"],
                    "user_id": _ui_user_id(),
                    "mount_path": root_path,
                    "label": os.path.basename(root_path.rstrip(os.sep)) or root_path,
                    "is_primary": True,
                }
            )
            general = db.upsert_workspace_project(
                {
                    "workspace_id": workspace["id"],
                    "user_id": _ui_user_id(),
                    "name": "General",
                    "description": f"Default project for {payload.name}",
                    "default_runtime": "codex",
                    "metadata": {"created_via": "sankhya-ui", "auto_created": True},
                }
            )
            db.replace_workspace_project_scope_rules(
                project_id=str(general.get("id") or ""),
                user_id=_ui_user_id(),
                rules=[{"path_prefix": root_path, "label": "root"}],
            )
            return {"ok": True, "workspace": _workspace_summary(db, workspace)}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/legacy-projects")
    def create_project_api(payload: WorkspaceRootCreatePayload) -> Dict[str, Any]:
        try:
            db = _get_db()
            workspace = db.upsert_workspace(
                {
                    "user_id": _ui_user_id(),
                    "name": payload.name,
                    "description": payload.description,
                    "metadata": {"created_via": "sankhya-ui"},
                }
            )
            return {"ok": True, "workspace": _workspace_summary(db, workspace)}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/legacy-projects/{project_id}")
    def get_project_api(project_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            workspace = db.get_workspace(project_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            return {"live": True, "workspace": _workspace_summary(db, workspace)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/canvas")
    def project_canvas_api(project_id: str) -> Dict[str, Any]:
        try:
            return _build_project_canvas_payload(project_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/workspaces")
    def list_project_workspaces_api(project_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            rows = db.list_project_workspaces(
                user_id=_ui_user_id(),
                project_id=project_id,
                limit=100,
            )
            return {
                "live": True,
                "workspaces": [_workspace_summary(db, row) for row in rows],
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/workspaces")
    def create_project_workspace_api(
        project_id: str,
        payload: WorkspaceCreatePayload,
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            workspace_path = os.path.abspath(os.path.expanduser(payload.workspace_path))
            workspace = db.upsert_project_workspace(
                {
                    "user_id": _ui_user_id(),
                    "project_id": project_id,
                    "workspace_path": workspace_path,
                    "label": payload.label or (os.path.basename(workspace_path) or workspace_path),
                    "folder_path": payload.folder_path or ".",
                    "is_primary": payload.is_primary,
                    "metadata": {
                        "created_via": "sankhya-ui",
                        "folders": [
                            {"path": os.path.abspath(os.path.expanduser(folder))}
                            for folder in (payload.folders or [])
                            if str(folder).strip()
                        ],
                    },
                }
            )
            return {"ok": True, "workspace": _workspace_summary(db, workspace)}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workspaces/{workspace_id}/projects")
    def list_workspace_projects_api(workspace_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            projects = [
                _project_summary(db, project)
                for project in db.list_workspace_projects(
                    workspace_id=workspace_id,
                    user_id=_ui_user_id(),
                    limit=100,
                )
            ]
            return {"live": True, "projects": projects}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/workspaces/{workspace_id}/projects")
    def create_workspace_project_api(
        workspace_id: str,
        payload: WorkspaceProjectCreatePayload,
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            project = db.upsert_workspace_project(
                {
                    "workspace_id": workspace_id,
                    "user_id": _ui_user_id(),
                    "name": payload.name,
                    "description": payload.description,
                    "default_runtime": payload.default_runtime or "codex",
                    "color": payload.color,
                    "icon": payload.icon,
                    "metadata": {"created_via": "sankhya-ui"},
                }
            )
            rules = payload.scope_rules or []
            if not rules:
                rules = [{"path_prefix": _workspace_primary_path(workspace), "label": "root"}]
            db.replace_workspace_project_scope_rules(
                project_id=str(project.get("id") or ""),
                user_id=_ui_user_id(),
                rules=rules,
            )
            return {"ok": True, "project": _project_summary(db, project)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}")
    def get_workspace_project_api(project_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            project = db.get_workspace_project(project_id, user_id=_ui_user_id())
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            return {"live": True, "project": _project_summary(db, project)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/projects/{project_id}")
    def update_workspace_project_api(
        project_id: str,
        payload: WorkspaceProjectUpdatePayload,
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            project = db.get_workspace_project(project_id, user_id=_ui_user_id())
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            updated = db.upsert_workspace_project(
                {
                    "id": project_id,
                    "workspace_id": project.get("workspace_id"),
                    "user_id": _ui_user_id(),
                    "name": str(payload.name or project.get("name") or "").strip() or project.get("name"),
                    "description": payload.description if payload.description is not None else project.get("description"),
                    "default_runtime": payload.default_runtime or project.get("default_runtime") or "codex",
                    "color": payload.color if payload.color is not None else project.get("color"),
                    "icon": payload.icon if payload.icon is not None else project.get("icon"),
                    "metadata": dict(project.get("metadata") or {}),
                }
            )
            if payload.scope_rules is not None:
                db.replace_workspace_project_scope_rules(
                    project_id=project_id,
                    user_id=_ui_user_id(),
                    rules=payload.scope_rules,
                )
            return {"ok": True, "project": _project_summary(db, updated)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/sessions")
    def list_workspace_project_sessions_api(project_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            project = db.get_workspace_project(project_id, user_id=_ui_user_id())
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            sessions = [
                _session_summary(db, session)
                for session in _workspace_project_sessions(db, project)
            ]
            return {"live": True, "sessions": sessions}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/folders/pick")
    def pick_folder_api(payload: Optional[FolderPickPayload] = None) -> Dict[str, Any]:
        try:
            prompt = (payload.prompt if payload else None) or "Select folder"
            safe_prompt = prompt.replace("\\", "\\\\").replace('"', '\\"')
            script = (
                f'set chosenFolder to choose folder with prompt "{safe_prompt}"\n'
                "POSIX path of chosenFolder"
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                if "User canceled" in detail or "User cancelled" in detail:
                    return {"ok": False, "cancelled": True}
                raise HTTPException(status_code=400, detail=detail or "Folder picker failed")
            return {"ok": True, "path": (result.stdout or "").strip()}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/workspaces/{workspace_id}/folders")
    @app.post("/api/workspaces/{workspace_id}/mounts")
    def add_workspace_folder_api(
        workspace_id: str,
        payload: WorkspaceFolderPayload,
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            resolved = os.path.abspath(os.path.expanduser(payload.path))
            mounts = db.list_workspace_mounts(workspace_id=workspace_id, user_id=_ui_user_id())
            if not any(str(mount.get("mount_path") or "") == resolved for mount in mounts):
                db.upsert_workspace_mount(
                    {
                        "workspace_id": workspace_id,
                        "user_id": _ui_user_id(),
                        "mount_path": resolved,
                        "label": payload.label or os.path.basename(resolved) or resolved,
                        "is_primary": not mounts,
                    }
                )
            updated = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not updated:
                raise HTTPException(status_code=404, detail="Workspace not found")
            return {"ok": True, "workspace": _workspace_summary(db, updated)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/workspaces/{workspace_id}")
    def update_workspace_api(
        workspace_id: str,
        payload: WorkspaceUpdatePayload,
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            next_name = str(payload.label or "").strip() or str(workspace.get("name") or "").strip()
            if not next_name:
                raise HTTPException(status_code=400, detail="Workspace name is required")
            next_description = (
                payload.description
                if payload.description is not None
                else workspace.get("description")
            )
            next_root = (
                str(payload.root_path).strip()
                if payload.root_path is not None and str(payload.root_path).strip()
                else _workspace_primary_path(workspace)
            )
            updated = db.upsert_workspace(
                {
                    "id": workspace_id,
                    "user_id": _ui_user_id(),
                    "name": next_name,
                    "description": next_description,
                    "root_path": next_root,
                    "metadata": dict(workspace.get("metadata") or {}),
                }
            )
            # Keep the mount table in sync so the workspace graph,
            # asset drawer path resolution, and folder list all see
            # the new primary root immediately.
            if payload.root_path is not None and str(payload.root_path).strip():
                resolved_root = os.path.abspath(os.path.expanduser(next_root))
                try:
                    # Demote any pre-existing primary mounts to non-primary.
                    for mount in db.list_workspace_mounts(workspace_id=workspace_id, user_id=_ui_user_id()):
                        if mount.get("is_primary") and str(mount.get("mount_path") or "") != resolved_root:
                            db.upsert_workspace_mount(
                                {
                                    "workspace_id": workspace_id,
                                    "user_id": _ui_user_id(),
                                    "mount_path": str(mount.get("mount_path") or ""),
                                    "label": mount.get("label"),
                                    "is_primary": False,
                                }
                            )
                    db.upsert_workspace_mount(
                        {
                            "workspace_id": workspace_id,
                            "user_id": _ui_user_id(),
                            "mount_path": resolved_root,
                            "label": os.path.basename(resolved_root.rstrip(os.sep)) or resolved_root,
                            "is_primary": True,
                        }
                    )
                except Exception:
                    pass
            return {"ok": True, "workspace": _workspace_summary(db, updated)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/workspaces/{workspace_id}")
    def delete_workspace_api(workspace_id: str) -> Dict[str, Any]:
        """Delete a workspace and everything scoped to it.

        Cascades through projects, mounts, line messages, project assets,
        and session assets. We do the cascade application-side so we can
        surface a friendly error if any step fails instead of leaving a
        half-deleted workspace behind.
        """
        try:
            db = _get_db()
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            ok = False
            if hasattr(db, "delete_workspace_cascade"):
                ok = bool(db.delete_workspace_cascade(workspace_id, user_id=_ui_user_id()))
            else:
                raise HTTPException(status_code=501, detail="Cascade delete not supported by this DB")
            return {"ok": ok, "id": workspace_id}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/projects/{project_id}")
    def delete_project_api(project_id: str) -> Dict[str, Any]:
        """Delete a project and its project-scoped assets + line messages.

        Workspace survives. Project-linked assets get their storage
        files removed best-effort; anything that fails logs silently
        rather than aborting the delete.
        """
        try:
            db = _get_db()
            project = db.get_workspace_project(project_id, user_id=_ui_user_id())
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            ok = False
            if hasattr(db, "delete_workspace_project_cascade"):
                # Best-effort asset file cleanup before the DB cascade drops rows.
                try:
                    for asset in db.list_project_assets(project_id=project_id, user_id=_ui_user_id()):
                        storage_path = str(asset.get("storage_path") or "")
                        if storage_path and os.path.exists(storage_path):
                            try:
                                os.remove(storage_path)
                            except Exception:
                                pass
                except Exception:
                    pass
                ok = bool(db.delete_workspace_project_cascade(project_id, user_id=_ui_user_id()))
            else:
                raise HTTPException(status_code=501, detail="Cascade delete not supported by this DB")
            return {"ok": ok, "id": project_id, "workspace_id": project.get("workspace_id")}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/workspaces/{workspace_id}/folders")
    @app.delete("/api/workspaces/{workspace_id}/mounts")
    def remove_workspace_folder_api(workspace_id: str, path: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            resolved = os.path.abspath(os.path.expanduser(path))
            mounts = _workspace_folder_mounts(
                {**workspace, "mounts": db.list_workspace_mounts(workspace_id=workspace_id, user_id=_ui_user_id())}
            )
            retained = [mount for mount in mounts if str(mount.get("path") or "") != resolved]
            if not retained:
                raise HTTPException(status_code=400, detail="Workspace must keep at least one mounted folder")
            primary_mount = next((mount for mount in retained if mount.get("primary")), None) or retained[0]
            for mount in mounts:
                db.delete_workspace_mount(
                    workspace_id=workspace_id,
                    mount_path=str(mount.get("path") or ""),
                    user_id=_ui_user_id(),
                )
            for mount in retained:
                db.upsert_workspace_mount(
                    {
                        "workspace_id": workspace_id,
                        "user_id": _ui_user_id(),
                        "mount_path": mount["path"],
                        "label": mount.get("label") or os.path.basename(mount["path"]) or mount["path"],
                        "is_primary": mount["path"] == primary_mount["path"],
                    }
                )
            updated = db.upsert_workspace(
                {
                    "id": workspace_id,
                    "user_id": _ui_user_id(),
                    "name": workspace.get("name"),
                    "description": workspace.get("description"),
                    "root_path": primary_mount["path"],
                    "metadata": dict(workspace.get("metadata") or {}),
                }
            )
            return {"ok": True, "workspace": _workspace_summary(db, updated)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workspaces/{workspace_id}")
    def get_workspace_api(workspace_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            return _workspace_detail_payload(db, workspace_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workspaces/{workspace_id}/sessions")
    def list_workspace_sessions_api(workspace_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            sessions = _workspace_sessions(db, workspace)[:100]
            return {
                "live": True,
                "sessions": [_session_summary(db, session) for session in sessions],
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workspaces/{workspace_id}/canvas")
    def get_workspace_canvas_api(workspace_id: str) -> Dict[str, Any]:
        try:
            return _build_workspace_canvas_payload(workspace_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/sessions/{session_id}")
    def get_session_api(session_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            return _session_detail_payload(db, session_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/sessions/{session_id}/messages")
    def get_session_messages_api(session_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            session = db.get_agent_session(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            return {"live": True, "messages": _session_messages_from_agent_session(session)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/assets")
    async def upload_session_asset_api(
        session_id: str,
        file: UploadFile = File(...),
        label: Optional[str] = Form(None),
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            session = db.get_agent_session(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            asset_root = Path(_dhee_data_dir_str()) / "session_assets" / session_id
            asset_root.mkdir(parents=True, exist_ok=True)
            filename = os.path.basename(file.filename or "asset")
            stored = asset_root / f"{int(time.time() * 1000)}-{filename}"
            size_bytes = 0
            with stored.open("wb") as handle:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    handle.write(chunk)
            artifact_id = None
            try:
                from dhee.core.artifacts import ArtifactManager

                manager = ArtifactManager(db)
                extracted_text = _extract_asset_text(str(stored), file.content_type)
                if extracted_text:
                    parsed = manager.capture_host_parse(
                        path=str(stored),
                        extracted_text=extracted_text,
                        user_id=_ui_user_id(),
                        cwd=str(session.get("cwd") or session.get("workspace_id") or ""),
                        harness=str(session.get("runtime_id") or "dhee"),
                        extraction_source="sankhya-upload",
                        project_id=session.get("project_id"),
                        metadata={
                            "label": label or filename,
                            "session_id": session_id,
                            "uploaded_via": "sankhya-ui",
                        },
                    )
                    artifact_id = str((parsed or {}).get("artifact_id") or "") or None
                else:
                    attached = manager.attach(
                        str(stored),
                        user_id=_ui_user_id(),
                        cwd=str(session.get("cwd") or session.get("workspace_id") or ""),
                        harness=str(session.get("runtime_id") or "dhee"),
                        project_id=session.get("project_id"),
                        metadata={
                            "label": label or filename,
                            "session_id": session_id,
                            "uploaded_via": "sankhya-ui",
                        },
                    )
                    artifact_id = str((attached or {}).get("artifact_id") or "") or None
            except Exception:
                artifact_id = None
            asset = db.add_session_asset(
                {
                    "project_id": session.get("project_id"),
                    "workspace_id": session.get("workspace_id"),
                    "session_id": session_id,
                    "user_id": _ui_user_id(),
                    "artifact_id": artifact_id,
                    "storage_path": str(stored),
                    "name": label or filename,
                    "mime_type": file.content_type,
                    "size_bytes": size_bytes,
                    "metadata": {"uploaded_via": "sankhya-ui", "original_name": filename},
                }
            )
            return {"ok": True, "asset": asset}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --------- Project / workspace assets (PR 3) ------------------------
    # Distinct from session assets: these live with the project and are
    # visible to every agent the workspace ever runs. Dedup by SHA-256
    # within (workspace, project) so re-uploads don't duplicate storage.

    async def _persist_project_asset(
        *,
        workspace_id: str,
        project_id: Optional[str],
        folder: Optional[str],
        file: UploadFile,
        label: Optional[str],
    ) -> Dict[str, Any]:
        db = _get_db()
        workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if project_id:
            project = db.get_workspace_project(project_id, user_id=_ui_user_id())
            if not project or str(project.get("workspace_id") or "") != workspace_id:
                raise HTTPException(status_code=404, detail="Project not found in workspace")

        asset_root = (
            Path(_dhee_data_dir_str())
            / "project_assets"
            / workspace_id
            / (project_id or "_workspace")
        )
        asset_root.mkdir(parents=True, exist_ok=True)
        filename = os.path.basename(file.filename or "asset")
        stored = asset_root / f"{int(time.time() * 1000)}-{filename}"

        size_bytes = 0
        hasher = hashlib.sha256()
        with stored.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                hasher.update(chunk)
                handle.write(chunk)
        checksum = hasher.hexdigest()

        artifact_id: Optional[str] = None
        try:
            from dhee.core.artifacts import ArtifactManager

            manager = ArtifactManager(db)
            extracted_text = _extract_asset_text(str(stored), file.content_type)
            if extracted_text:
                parsed = manager.capture_host_parse(
                    path=str(stored),
                    extracted_text=extracted_text,
                    user_id=_ui_user_id(),
                    cwd=str(workspace.get("root_path") or ""),
                    harness="dhee",
                    extraction_source="sankhya-upload",
                    project_id=project_id,
                    metadata={
                        "label": label or filename,
                        "uploaded_via": "sankhya-ui",
                        "scope": "project" if project_id else "workspace",
                    },
                )
                artifact_id = str((parsed or {}).get("artifact_id") or "") or None
            else:
                attached = manager.attach(
                    str(stored),
                    user_id=_ui_user_id(),
                    cwd=str(workspace.get("root_path") or ""),
                    harness="dhee",
                    project_id=project_id,
                    metadata={
                        "label": label or filename,
                        "uploaded_via": "sankhya-ui",
                        "scope": "project" if project_id else "workspace",
                    },
                )
                artifact_id = str((attached or {}).get("artifact_id") or "") or None
        except Exception:
            artifact_id = None

        asset = db.upsert_project_asset(
            {
                "workspace_id": workspace_id,
                "project_id": project_id,
                "user_id": _ui_user_id(),
                "artifact_id": artifact_id,
                "folder": folder,
                "storage_path": str(stored),
                "name": label or filename,
                "mime_type": file.content_type,
                "size_bytes": size_bytes,
                "checksum": checksum,
                "metadata": {
                    "uploaded_via": "sankhya-ui",
                    "original_name": filename,
                    "scope": "project" if project_id else "workspace",
                },
            }
        )

        # Announce the upload on the workspace information line so sibling
        # agents see that a new asset is available.
        try:
            from dhee.core.workspace_line import emit_agent_activity

            emit_agent_activity(
                db,
                user_id=_ui_user_id(),
                tool_name="Upload",
                packet_kind="asset_upload",
                digest=f"asset uploaded · {asset.get('name')}",
                cwd=str(workspace.get("root_path") or ""),
                source_path=str(stored),
                source_event_id=str(asset.get("id") or ""),
                artifact_id=artifact_id,
                harness="dhee",
                agent_id="dhee",
                metadata={
                    "asset_id": asset.get("id"),
                    "scope": "project" if project_id else "workspace",
                    "project_id": project_id,
                    "workspace_id": workspace_id,
                    "size_bytes": size_bytes,
                    "checksum": checksum,
                },
            )
        except Exception:
            pass

        return asset

    def _project_asset_results_payload(
        db: Any, asset: Dict[str, Any], *, limit: int = 16
    ) -> List[Dict[str, Any]]:
        """Recent shared-task results that mention this asset's storage path.

        This is what makes the drawer feel alive: "Claude read it 4m ago",
        "Codex grepped it 12m ago". We match on storage_path (exact) and
        on the matching artifact_id when present, to cover the router
        path (which only knows ptrs) and the hook path (which knows files).
        """
        storage_path = str(asset.get("storage_path") or "")
        if not storage_path:
            return []
        # Match only on source_path. `shared_tasks.workspace_id` is a path
        # string for router-tracked sessions, not a UUID — filtering on
        # `project_assets.workspace_id` (a UUID) would always miss.
        # source_path is unique enough.
        try:
            return db.list_shared_task_results_for_path(
                user_id=_ui_user_id(),
                source_path=storage_path,
                limit=limit,
            )
        except Exception:
            return []

    def _project_asset_to_payload(db: Any, asset: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **asset,
            "results": _project_asset_results_payload(db, asset),
        }

    @app.get("/api/projects/{project_id}/assets")
    def list_project_assets_api(project_id: str, limit: int = 100) -> Dict[str, Any]:
        try:
            db = _get_db()
            project = db.get_workspace_project(project_id, user_id=_ui_user_id())
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            assets = db.list_project_assets(
                project_id=project_id, user_id=_ui_user_id(), limit=limit
            )
            return {
                "live": True,
                "project_id": project_id,
                "workspace_id": project.get("workspace_id"),
                "assets": [_project_asset_to_payload(db, asset) for asset in assets],
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/assets")
    async def upload_project_asset_api(
        project_id: str,
        file: UploadFile = File(...),
        label: Optional[str] = Form(None),
        folder: Optional[str] = Form(None),
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            project = db.get_workspace_project(project_id, user_id=_ui_user_id())
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            asset = await _persist_project_asset(
                workspace_id=str(project.get("workspace_id") or ""),
                project_id=project_id,
                folder=folder,
                file=file,
                label=label,
            )
            return {"ok": True, "asset": _project_asset_to_payload(db, asset)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workspaces/{workspace_id}/assets")
    def list_workspace_assets_api(
        workspace_id: str,
        include_project_assets: bool = True,
        limit: int = 200,
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            assets = db.list_workspace_assets(
                workspace_id=workspace_id,
                user_id=_ui_user_id(),
                include_project_assets=include_project_assets,
                limit=limit,
            )
            return {
                "live": True,
                "workspace_id": workspace_id,
                "assets": [_project_asset_to_payload(db, asset) for asset in assets],
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/workspaces/{workspace_id}/assets")
    async def upload_workspace_asset_api(
        workspace_id: str,
        file: UploadFile = File(...),
        label: Optional[str] = Form(None),
        folder: Optional[str] = Form(None),
        project_id: Optional[str] = Form(None),
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            asset = await _persist_project_asset(
                workspace_id=workspace_id,
                project_id=project_id or None,
                folder=folder,
                file=file,
                label=label,
            )
            return {"ok": True, "asset": _project_asset_to_payload(db, asset)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/project-assets/{asset_id}")
    def delete_project_asset_api(asset_id: str) -> Dict[str, Any]:
        try:
            db = _get_db()
            asset = db.get_project_asset(asset_id)
            if not asset:
                raise HTTPException(status_code=404, detail="Asset not found")
            ok = db.delete_project_asset(asset_id, user_id=_ui_user_id())
            if ok:
                try:
                    storage_path = str(asset.get("storage_path") or "")
                    if storage_path and os.path.exists(storage_path):
                        os.remove(storage_path)
                except Exception:
                    pass
            return {"ok": bool(ok)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/files/{file_id:path}/context")
    def file_context_api(file_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            return {"live": True, **_file_context_payload(_get_db(), file_id, workspace_id=workspace_id)}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/assets/{asset_id}/context")
    def asset_context_api(asset_id: str) -> Dict[str, Any]:
        try:
            return {"live": True, **_asset_context_payload(_get_db(), asset_id)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/assets/{asset_id}/ask")
    def ask_asset_api(asset_id: str, payload: AssetAskPayload) -> Dict[str, Any]:
        try:
            db = _get_db()
            context = _asset_context_payload(db, asset_id)
            asset = context.get("asset") or {}
            session = context.get("session") or {}
            workspace_id = str(asset.get("workspace_id") or session.get("workspace_id") or "")
            if not workspace_id:
                raise HTTPException(status_code=400, detail="Asset is not attached to a workspace")
            question = str(payload.question or "").strip()
            if not question:
                raise HTTPException(status_code=400, detail="Question is required")
            launch = launch_workspace_session(
                workspace_id,
                SessionLaunchPayload(
                    runtime="claude-code",
                    title=f"Ask {asset.get('name') or 'asset'}",
                    permission_mode="standard",
                ),
            )
            task_id = str(launch.get("task_id") or "")
            if task_id:
                chunk_text = "\n\n".join(
                    f"chunk {chunk.get('chunk_index')}: {chunk.get('content')}"
                    for chunk in (context.get("chunks") or [])[:4]
                ).strip()
                db.save_shared_task_result(
                    {
                        "shared_task_id": task_id,
                        "project_id": asset.get("project_id"),
                        "workspace_id": workspace_id,
                        "result_key": f"asset-ask:{asset_id}:{int(time.time())}",
                        "packet_kind": "note",
                        "tool_name": "user_note",
                        "result_status": "completed",
                        "source_path": str(asset.get("storage_path") or ""),
                        "artifact_id": asset.get("artifact_id"),
                        "digest": (
                            f"Answer the following question using the attached asset '{asset.get('name')}'.\n\n"
                            f"Question: {question}\n\n"
                            f"Known extracted context:\n{chunk_text or context.get('summary') or 'No extracted text available yet.'}"
                        ),
                        "metadata": {
                            "source": "asset_ask",
                            "asset_id": asset_id,
                            "question": question,
                        },
                    }
                )
            return {"ok": True, "launch": launch, "asset": asset, "question": question}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ─── Tasks ───────────────────────────────────────────────────────────────

    @app.get("/api/tasks")
    def tasks() -> Dict[str, Any]:
        """Repo-scoped shared tasks → UI task cards."""
        try:
            db = _get_db()
            sync = _mirror_runtime_sessions(db)
            user_id = _ui_user_id()
            repo = _ui_repo()
            rows = db.list_shared_tasks(
                user_id=user_id,
                project_id=str((sync.get("project") or {}).get("id") or "") or None,
                repo=repo,
                limit=48,
            )
            out = [
                _shared_task_to_ui_task(db, row, index=i)
                for i, row in enumerate(rows)
                if isinstance(row, dict)
            ]
            return {"live": True, "tasks": out, "repo": repo}
        except Exception as exc:  # noqa: BLE001
            log.warning("tasks failed: %s", exc)
            return {"live": False, "tasks": [], "error": str(exc)}

    class TaskCreate(BaseModel):
        title: str
        harness: Optional[str] = None

    class TaskStatusUpdate(BaseModel):
        status: str

    class TaskNotePayload(BaseModel):
        content: str

    @app.post("/api/tasks")
    def create_task(payload: TaskCreate) -> Dict[str, Any]:
        try:
            db = _get_db()
            sync = _mirror_runtime_sessions(db)
            task = db.upsert_shared_task(
                {
                    "user_id": _ui_user_id(),
                    "project_id": (sync.get("project") or {}).get("id"),
                    "repo": _ui_repo(),
                    "workspace_id": (sync.get("workspace") or {}).get("id"),
                    "folder_path": (sync.get("workspace") or {}).get("folder_path"),
                    "title": payload.title,
                    "status": "paused",
                    "created_by": "sankhya-ui",
                    "metadata": {
                        "harness": _normalize_runtime(payload.harness),
                        "created_via": "sankhya-ui",
                    },
                }
            )
            return {
                "ok": True,
                "task": _shared_task_to_ui_task(db, task, index=0),
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str, limit: int = 24) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            task = _get_shared_task_or_404(db, task_id)
            ui_task = _shared_task_to_ui_task(db, task, index=0, result_limit=limit)
            results = db.list_shared_task_results(shared_task_id=task_id, limit=limit)
            return {
                "live": True,
                "task": ui_task,
                "results": results,
                "runtime": _get_runtime_status_payload(),
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/status")
    def task_status_update(task_id: str, payload: TaskStatusUpdate) -> Dict[str, Any]:
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            task = _get_shared_task_or_404(db, task_id)
            status = str(payload.status or "").strip().lower()
            if status not in {"active", "paused", "completed", "closed", "abandoned"}:
                raise HTTPException(status_code=400, detail="Unsupported task status")
            updated = _touch_shared_task(
                db,
                task,
                status=status,
                metadata_updates={"updated_via": "sankhya-ui"},
            )
            return {
                "ok": True,
                "task": _shared_task_to_ui_task(db, updated, index=0, result_limit=12),
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/notes")
    def task_note_add(task_id: str, payload: TaskNotePayload) -> Dict[str, Any]:
        try:
            content = str(payload.content or "").strip()
            if not content:
                raise HTTPException(status_code=400, detail="content is required")
            db = _get_db()
            _mirror_runtime_sessions(db)
            task = _get_shared_task_or_404(db, task_id)
            metadata = dict(task.get("metadata") or {})
            result_id = db.save_shared_task_result(
                {
                    "result_key": f"{task_id}:note:{int(time.time() * 1000)}",
                    "shared_task_id": task_id,
                    "user_id": _ui_user_id(),
                    "project_id": task.get("project_id"),
                    "repo": task.get("repo") or _ui_repo(),
                    "workspace_id": task.get("workspace_id"),
                    "folder_path": task.get("folder_path"),
                    "packet_kind": "note",
                    "tool_name": "user_note",
                    "result_status": "completed",
                    "digest": content,
                    "metadata": {
                        "created_via": "sankhya-ui",
                        "kind": "task_note",
                        "task_title": task.get("title"),
                        "harness": metadata.get("harness"),
                    },
                    "harness": _normalize_runtime(metadata.get("harness")),
                    "agent_id": "sankhya-ui",
                }
            )
            updated = _touch_shared_task(
                db,
                task,
                metadata_updates={"updated_via": "sankhya-ui"},
            )
            return {
                "ok": True,
                "task": _shared_task_to_ui_task(db, updated, index=0, result_limit=12),
                "result": db.get_shared_task_result(result_id),
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ─── Pointer Capture Memory OS ─────────────────────────────────────────

    @app.post("/api/capture/session/start")
    def capture_session_start(payload: CaptureSessionStartPayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().start_capture_session(
                user_id=_ui_user_id(),
                source_app=payload.source_app,
                namespace=payload.namespace,
                metadata=payload.metadata,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/capture/session/{session_id}/end")
    def capture_session_end(session_id: str, payload: CaptureSessionEndPayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().end_capture_session(
                session_id,
                distill=payload.distill,
                summary_hint=payload.summary_hint or "",
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/capture/session/end")
    def capture_session_end_legacy(payload: CaptureSessionEndPayload) -> Dict[str, Any]:
        session_id = str(payload.session_id or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        return capture_session_end(session_id, payload)

    @app.get("/api/capture/session/{session_id}")
    def capture_session_get(session_id: str) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().get_capture_session(session_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/capture/action")
    def capture_action(payload: CaptureActionPayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().record_action(payload.model_dump(exclude_none=True))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/capture/navigation")
    def capture_navigation(payload: CaptureActionPayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().record_navigation(payload.model_dump(exclude_none=True))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/capture/observation")
    def capture_observation(payload: CaptureObservationPayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().record_observation(payload.model_dump(exclude_none=True))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/capture/artifact")
    def capture_artifact(payload: CaptureArtifactPayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().record_artifact(payload.model_dump(exclude_none=True))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/capture/timeline")
    def capture_timeline(source_app: Optional[str] = None, limit: int = 30) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().timeline(
                user_id=_ui_user_id(),
                source_app=source_app,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/capture/preferences")
    def capture_preferences() -> Dict[str, Any]:
        try:
            return _get_memory_os_service().list_capture_policies()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/capture/preferences")
    def capture_preferences_set(payload: CapturePreferencePayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().set_capture_policy(
                source_app=payload.source_app,
                enabled=payload.enabled,
                mode=payload.mode,
                metadata=payload.metadata,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/memory/now")
    def memory_now(source_app: Optional[str] = None, limit: int = 8) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().memory_now(
                user_id=_ui_user_id(),
                source_app=source_app,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/memory/ask")
    def memory_ask(payload: MemoryAskPayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().memory_ask(
                user_id=_ui_user_id(),
                query=payload.query,
                source_app=payload.source_app,
                limit=payload.limit,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/world-memory/context-pack")
    def world_context_pack(payload: WorldContextPackPayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().world_context_pack(
                user_id=_ui_user_id(),
                current_frame_ref=payload.current_frame_ref,
                current_context_text=payload.current_context_text,
                task_instruction=payload.task_instruction,
                recent_actions=payload.recent_actions,
                limit=payload.limit,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/agents/context-pack")
    def agents_context_pack(payload: AgentContextPackPayload) -> Dict[str, Any]:
        try:
            return _get_memory_os_service().agent_context_pack(
                user_id=_ui_user_id(),
                agent_id=payload.agent_id,
                task_instruction=payload.task_instruction,
                source_app=payload.source_app,
                current_frame_ref=payload.current_frame_ref or "",
                current_context_text=payload.current_context_text or "",
                recent_actions=payload.recent_actions,
                limit=payload.limit,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ─── Security / API Keys ────────────────────────────────────────────────

    class ApiKeyPayload(BaseModel):
        provider: str
        apiKey: str
        label: Optional[str] = None

    class RotateApiKeyPayload(BaseModel):
        apiKey: str
        label: Optional[str] = None

    @app.get("/api/security/api-keys")
    def list_api_keys() -> Dict[str, Any]:
        try:
            from dhee import secret_store

            return {"live": True, "providers": secret_store.list_provider_statuses()}
        except Exception as exc:  # noqa: BLE001
            log.warning("list_api_keys failed: %s", exc)
            return {"live": False, "providers": [], "error": str(exc)}

    @app.post("/api/security/api-keys")
    def store_api_key(payload: ApiKeyPayload) -> Dict[str, Any]:
        try:
            from dhee import secret_store

            provider = secret_store.store_api_key(
                payload.provider,
                payload.apiKey,
                label=payload.label,
            )
            return {"ok": True, "provider": provider}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/security/api-keys/{provider}/rotate")
    def rotate_api_key(provider: str, payload: RotateApiKeyPayload) -> Dict[str, Any]:
        try:
            from dhee import secret_store

            rotated = secret_store.rotate_api_key(
                provider,
                payload.apiKey,
                label=payload.label,
            )
            return {"ok": True, "provider": rotated}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ─── Status + Launch ─────────────────────────────────────────────────────

    @app.get("/api/status")
    def status() -> Dict[str, Any]:
        try:
            from dhee.router import stats as rstats

            s = rstats.compute_stats()
            return {
                "ok": True,
                "router": {
                    "sessions": s.sessions,
                    "calls": s.total_calls,
                    "tokensSaved": s.est_tokens_diverted,
                },
                "dhee_data_dir": _dhee_data_dir_str(),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @app.get("/api/runtime-status")
    def runtime_status() -> Dict[str, Any]:
        try:
            return _get_runtime_status_payload()
        except Exception as exc:  # noqa: BLE001
            log.warning("runtime_status failed: %s", exc)
            return {
                "live": False,
                "repo": _ui_repo(),
                "runtimes": [],
                "error": str(exc),
            }

    class LaunchPayload(BaseModel):
        taskId: Optional[str] = None
        runtime: str  # claude-code | codex | both
        title: Optional[str] = None
        permission_mode: Optional[str] = None

    @app.post("/api/workspaces/{workspace_id}/sessions/launch")
    def launch_workspace_session(
        workspace_id: str,
        payload: SessionLaunchPayload,
    ) -> Dict[str, Any]:
        runtime = _normalize_runtime(payload.runtime)
        if runtime is None:
            raise HTTPException(status_code=400, detail="Unsupported runtime")
        try:
            db = _get_db()
            _mirror_runtime_sessions(db)
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            project = None
            if payload.project_id:
                project = db.get_workspace_project(payload.project_id, user_id=_ui_user_id())
            if not project:
                project = _resolve_workspace_project_for_path(
                    db,
                    workspace_id=workspace_id,
                    path=_workspace_primary_path(workspace),
                )
            if not project:
                projects = db.list_workspace_projects(
                    workspace_id=workspace_id,
                    user_id=_ui_user_id(),
                    limit=20,
                )
                project = (projects[0] if projects else None) or _ensure_unassigned_workspace_project(db, workspace)
            existing = None
            if payload.task_id:
                existing = db.get_shared_task(payload.task_id, user_id=_ui_user_id())
            title = str(payload.title or (existing or {}).get("title") or "").strip()
            if not title:
                raise HTTPException(status_code=400, detail="Task title is required")
            permission_mode = _normalize_permission_mode(runtime, payload.permission_mode)
            native_seed = f"{runtime}:{workspace_id}:{int(time.time() * 1000)}"
            session_id = _agent_session_id(runtime, native_seed)
            launch_cwd = _workspace_primary_path(workspace)
            scope_rules = _workspace_project_scope_rules(db, str(project.get("id") or ""))
            if scope_rules:
                launch_cwd = str((scope_rules[0] or {}).get("path_prefix") or launch_cwd)
            task = db.upsert_shared_task(
                {
                    "id": (existing or {}).get("id") or _session_task_id(runtime, native_seed),
                    "user_id": _ui_user_id(),
                    "project_id": project.get("id") if project else None,
                    "repo": _ui_repo(),
                    "workspace_id": workspace_id,
                    "folder_path": ".",
                    "session_id": session_id,
                    "thread_id": native_seed,
                    "runtime_id": runtime,
                    "native_session_id": native_seed,
                    "title": title,
                    "status": "active",
                    "created_by": "sankhya-ui",
                    "metadata": {
                        "harness": runtime,
                        "created_via": "sankhya-ui",
                        "launch_requested": True,
                        "permission_mode": permission_mode,
                    },
                }
            )
            session = db.upsert_agent_session(
                {
                    "id": session_id,
                    "project_id": project.get("id") if project else None,
                    "workspace_id": workspace_id,
                    "user_id": _ui_user_id(),
                    "runtime_id": runtime,
                    "native_session_id": native_seed,
                    "task_id": task.get("id"),
                    "title": title,
                    "state": "launch-requested",
                    "cwd": launch_cwd,
                    "permission_mode": permission_mode,
                    "updated_at": _now_iso(),
                    "metadata": {
                        "messages": [],
                        "recent_tools": [],
                        "plan": [],
                        "touched_files": [],
                        "rate_limits": {},
                        "preview": "Launch requested from Dhee.",
                    },
                }
            )
            if runtime == "claude-code":
                launch_command = f'cd "{launch_cwd}" && claude'
                if permission_mode == "full-access":
                    launch_command += " --dangerously-skip-permissions"
            elif runtime == "codex":
                launch_command = f'cd "{launch_cwd}" && codex'
            else:
                launch_command = f'cd "{launch_cwd}" && dhee install --harness all'
            return {
                "ok": True,
                "project_id": project.get("id") if project else None,
                "workspace_id": workspace_id,
                "session_id": session.get("id"),
                "task_id": task.get("id"),
                "runtime": runtime,
                "permission_mode": permission_mode,
                "launch_command": launch_command,
                "control_state": "mirrored",
                "session": _session_summary(db, session),
                "task": _shared_task_to_ui_task(db, task, index=0, result_limit=12),
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/workspaces/{workspace_id}/line/messages")
    def list_workspace_line_messages_api(
        workspace_id: str,
        project_id: Optional[str] = None,
        channel: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            rows = db.list_workspace_line_messages(
                workspace_id=workspace_id,
                user_id=_ui_user_id(),
                project_id=project_id,
                channel=channel,
                cursor=cursor,
                limit=limit,
            )
            return {
                "live": True,
                "messages": rows,
                "nextCursor": _line_cursor(rows[-1] if rows else None),
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/workspaces/{workspace_id}/line/messages")
    def publish_workspace_line_message_api(
        workspace_id: str,
        payload: WorkspaceLineMessagePayload,
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            source_project_id = str(payload.project_id or "").strip() or None
            target_project_id = str(payload.target_project_id or "").strip() or None
            suggested_task = None
            if target_project_id and target_project_id != source_project_id:
                suggested_task = _create_suggested_task_from_broadcast(
                    db,
                    workspace_id=workspace_id,
                    project_id=target_project_id,
                    source_project_id=source_project_id,
                    title=str(payload.title or "Workspace broadcast").strip() or "Workspace broadcast",
                    body=str(payload.body or "").strip(),
                    session_id=payload.session_id,
                )
            message = db.add_workspace_line_message(
                {
                    "workspace_id": workspace_id,
                    "project_id": source_project_id,
                    "target_project_id": target_project_id,
                    "user_id": _ui_user_id(),
                    "channel": payload.channel or ("project" if source_project_id else "workspace"),
                    "session_id": payload.session_id,
                    "task_id": (suggested_task or {}).get("id") or payload.task_id,
                    "message_kind": payload.message_kind,
                    "title": payload.title,
                    "body": payload.body,
                    "metadata": payload.metadata or {},
                }
            )
            # Fan this onto the in-process bus so every SSE subscriber on
            # this workspace gets it in the same tick — no 1s poll lag.
            if message:
                try:
                    from dhee.core.workspace_line_bus import publish as _publish_bus

                    _publish_bus(message)
                except Exception:
                    pass
            return {
                "ok": True,
                "message": message,
                "suggestedTask": _shared_task_to_ui_task(db, suggested_task, index=0, result_limit=8)
                if suggested_task
                else None,
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workspaces/{workspace_id}/line/stream")
    async def workspace_line_stream_api(
        workspace_id: str,
        project_id: Optional[str] = None,
        channel: Optional[str] = None,
        cursor: Optional[str] = None,
        backfill: int = 0,
    ) -> StreamingResponse:
        """Real-time line stream via an in-process pub/sub bus.

        Replaces the prior 1s DB-poll loop. Each subscription gets
        messages pushed in the same event-loop tick as the write, with
        heartbeat keep-alives every 15s. A best-effort ``backfill``
        parameter lets the client catch up on the N most recent
        messages on (re)connect without a separate REST call.
        """
        from dhee.core.workspace_line_bus import iter_messages

        async def gen():
            db = _get_db()
            # Optional backfill — send the most recent N messages so a
            # reconnect doesn't require a separate REST hop.
            if backfill and backfill > 0:
                try:
                    rows = db.list_workspace_line_messages(
                        workspace_id=workspace_id,
                        user_id=_ui_user_id(),
                        project_id=project_id,
                        channel=channel,
                        cursor=cursor,
                        limit=min(int(backfill), 100),
                    )
                    for row in reversed(rows):
                        yield f"data: {json.dumps(row)}\n\n"
                except Exception:
                    pass

            try:
                async for message in iter_messages(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    channel=channel,
                ):
                    if message is None:
                        yield ": keep-alive\n\n"
                    else:
                        yield f"data: {json.dumps(message)}\n\n"
            except asyncio.CancelledError:
                return

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/projects/{project_id}/tasks/from-broadcast")
    def create_task_from_broadcast_api(
        project_id: str,
        payload: WorkspaceLineMessagePayload,
    ) -> Dict[str, Any]:
        try:
            db = _get_db()
            project = db.get_workspace_project(project_id, user_id=_ui_user_id())
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            task = _create_suggested_task_from_broadcast(
                db,
                workspace_id=str(project.get("workspace_id") or ""),
                project_id=project_id,
                source_project_id=str(payload.project_id or "").strip() or None,
                title=str(payload.title or "Workspace broadcast").strip() or "Workspace broadcast",
                body=str(payload.body or "").strip(),
                session_id=payload.session_id,
            )
            return {"ok": True, "task": _shared_task_to_ui_task(db, task, index=0, result_limit=8)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/launch")
    def launch(payload: LaunchPayload) -> Dict[str, Any]:
        try:
            db = _get_db()
            sync = _mirror_runtime_sessions(db)
            workspace_id = str((sync.get("workspace") or {}).get("id") or "")
            if not workspace_id:
                raise HTTPException(status_code=400, detail="No workspace available")
            launched = launch_workspace_session(
                workspace_id,
                SessionLaunchPayload(
                    runtime=payload.runtime,
                    title=payload.title,
                    permission_mode=payload.permission_mode,
                    task_id=payload.taskId,
                ),
            )
            launched["taskId"] = launched.get("task_id")
            launched["command"] = launched.get("launch_command")
            launched["message"] = "Session prepared in Dhee. Open the native runtime with the launch command."
            return launched
        except HTTPException:
            raise

    @app.get("/api/workspace/graph")
    def workspace_graph(
        workspace_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            if workspace_id:
                return _build_workspace_canvas_payload(workspace_id, focus_project_id=project_id)
            return _build_workspace_graph_payload()
        except Exception as exc:  # noqa: BLE001
            log.warning("workspace_graph failed: %s", exc)
            return {
                "live": False,
                "repo": _ui_repo(),
                "graph": {"nodes": [], "links": []},
                "sessions": [],
                "tasks": [],
                "files": [],
                "workspaces": [],
                "error": str(exc),
            }

    # ─── Static SPA ──────────────────────────────────────────────────────────

    if serve_static:
        dist = Path(__file__).parent / "web" / "dist"
        if dist.exists():
            app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")
        else:
            @app.get("/")
            def _no_build() -> Dict[str, str]:
                return {
                    "error": "SPA not built. Run `cd dhee/ui/web && npm install && npm run build`.",
                    "dist_expected": str(dist),
                }

    if dev_mode:
        import httpx
        client = httpx.AsyncClient(base_url="http://127.0.0.1:5173")

        @app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
        async def proxy_vite(path_name: str, request: Request):
            url = httpx.URL(path=path_name, query=request.url.query.encode("utf-8"))
            rp_req = client.build_request(
                request.method,
                url,
                headers=request.headers.raw,
                content=await request.body(),
            )
            rp_resp = await client.send(rp_req, stream=True)
            from fastapi.responses import StreamingResponse
            return StreamingResponse(
                rp_resp.aiter_raw(),
                status_code=rp_resp.status_code,
                headers=rp_resp.headers,
                background=None, # type: ignore
            )

        @app.websocket("/{path_name:path}")
        async def proxy_vite_ws(path_name: str, websocket: WebSocket):
            import websockets
            await websocket.accept()
            try:
                # Vite HMR is usually at the root or / (empty path_name)
                # But we'll try to connect to Vite's port 5173
                vite_ws_url = f"ws://127.0.0.1:5173/{path_name}"
                async with websockets.connect(vite_ws_url) as vite_ws:
                    # Bi-directional proxy
                    async def forward_to_vite():
                        try:
                            while True:
                                data = await websocket.receive_text()
                                await vite_ws.send(data)
                        except Exception:
                            pass

                    async def forward_from_vite():
                        try:
                            async for message in vite_ws:
                                await websocket.send_text(str(message))
                        except Exception:
                            pass

                    import asyncio
                    await asyncio.gather(forward_to_vite(), forward_from_vite())
            except WebSocketDisconnect:
                pass
            except Exception as e:
                log.debug("Vite WS Proxy error: %s", e)
            finally:
                try:
                    await websocket.close()
                except Exception:
                    pass

    return app


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ui_user_id() -> str:
    return str(
        os.environ.get("DHEE_UI_USER_ID")
        or os.environ.get("DHEE_USER_ID")
        or "default"
    )


def _ui_repo() -> str:
    return os.path.abspath(
        os.path.expanduser(os.environ.get("DHEE_UI_REPO") or os.getcwd())
    )


def _get_db():
    from dhee.mcp_server import get_db

    return get_db()


def _auto_project_name(repo: str) -> str:
    return os.path.basename(repo.rstrip(os.sep)) or "Project"


def _workspace_folder_mounts(workspace: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not workspace:
        return []
    explicit_mounts = workspace.get("mounts") or workspace.get("folders")
    if isinstance(explicit_mounts, list) and explicit_mounts:
        mounts: List[Dict[str, Any]] = []
        for item in explicit_mounts:
            if isinstance(item, str):
                raw_path = item
                label = ""
                primary = False
            elif isinstance(item, dict):
                raw_path = str(item.get("path") or item.get("mount_path") or "").strip()
                label = str(item.get("label") or "").strip()
                primary = bool(item.get("primary") or item.get("is_primary"))
            else:
                continue
            if not raw_path:
                continue
            resolved = os.path.abspath(os.path.expanduser(raw_path))
            if any(existing["path"] == resolved for existing in mounts):
                continue
            mounts.append(
                {
                    "path": resolved,
                    "label": label or os.path.basename(resolved) or resolved,
                    "primary": primary or not mounts,
                }
            )
        if mounts:
            mounts[0]["primary"] = any(mount.get("primary") for mount in mounts) or True
            return mounts
    metadata = dict(workspace.get("metadata") or {})
    mounts: List[Dict[str, Any]] = []
    primary_path = str(
        workspace.get("workspace_path")
        or workspace.get("root_path")
        or ""
    ).strip()
    if primary_path:
        mounts.append(
            {
                "path": os.path.abspath(os.path.expanduser(primary_path)),
                "label": str(workspace.get("label") or os.path.basename(primary_path) or primary_path),
                "primary": True,
            }
        )
    for item in metadata.get("folders") or []:
        if isinstance(item, str):
            raw_path = item
            label = ""
        elif isinstance(item, dict):
            raw_path = str(item.get("path") or "").strip()
            label = str(item.get("label") or "").strip()
        else:
            continue
        if not raw_path:
            continue
        resolved = os.path.abspath(os.path.expanduser(raw_path))
        if any(existing["path"] == resolved for existing in mounts):
            continue
        mounts.append(
            {
                "path": resolved,
                "label": label or os.path.basename(resolved) or resolved,
                "primary": False,
            }
        )
    return mounts


def _workspace_contains_path(workspace: Optional[Dict[str, Any]], path: Optional[str]) -> bool:
    raw = str(path or "").strip()
    if not workspace or not raw:
        return False
    candidate = os.path.abspath(os.path.expanduser(raw))
    for mount in _workspace_folder_mounts(workspace):
        mount_path = str(mount.get("path") or "").strip()
        if not mount_path:
            continue
        try:
            common = os.path.commonpath([candidate, mount_path])
        except ValueError:
            continue
        if common == mount_path:
            return True
    return False


def _workspace_primary_path(workspace: Optional[Dict[str, Any]]) -> str:
    mounts = _workspace_folder_mounts(workspace)
    primary = next((mount for mount in mounts if mount.get("primary")), None) or (mounts[0] if mounts else None)
    return str((primary or {}).get("path") or workspace.get("root_path") or "").strip()


def _workspace_project_scope_rules(db: Any, project_id: str) -> List[Dict[str, Any]]:
    try:
        return db.list_workspace_project_scope_rules(project_id=project_id, user_id=_ui_user_id())
    except Exception:
        return []


def _resolve_workspace_project_for_path(
    db: Any,
    *,
    workspace_id: str,
    path: Optional[str],
) -> Optional[Dict[str, Any]]:
    raw = str(path or "").strip()
    if not workspace_id:
        return None
    candidate = os.path.abspath(os.path.expanduser(raw)) if raw else ""
    best: Optional[Dict[str, Any]] = None
    best_len = -1
    for project in db.list_workspace_projects(
        workspace_id=workspace_id,
        user_id=_ui_user_id(),
        limit=200,
    ):
        for rule in _workspace_project_scope_rules(db, str(project.get("id") or "")):
            prefix = str(rule.get("path_prefix") or "").strip()
            if not prefix or not candidate:
                continue
            try:
                common = os.path.commonpath([candidate, prefix])
            except ValueError:
                continue
            if common != prefix:
                continue
            if len(prefix) > best_len:
                best = project
                best_len = len(prefix)
    return best


def _ensure_unassigned_workspace_project(
    db: Any,
    workspace: Dict[str, Any],
) -> Dict[str, Any]:
    workspace_id = str(workspace.get("id") or "")
    for project in db.list_workspace_projects(
        workspace_id=workspace_id,
        user_id=_ui_user_id(),
        limit=200,
    ):
        if str(project.get("name") or "").strip().lower() == "unassigned":
            return project
    return db.upsert_workspace_project(
        {
            "workspace_id": workspace_id,
            "user_id": _ui_user_id(),
            "name": "Unassigned",
            "description": "Sessions that do not match any explicit project scope rule.",
            "default_runtime": "codex",
            "metadata": {"system": True, "auto_created": True},
        }
    )


def _normalize_permission_mode(runtime: Optional[str], value: Optional[str]) -> str:
    normalized_runtime = _normalize_runtime(runtime)
    raw = str(value or "").strip().lower()
    if normalized_runtime != "claude-code":
        return "native"
    if raw in {"full-access", "full_access", "bypasspermissions", "bypass"}:
        return "full-access"
    return "standard"


def _ensure_default_project_workspace(db: Any, repo: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    user_id = _ui_user_id()
    repo_abs = os.path.abspath(os.path.expanduser(repo))
    workspaces = db.list_workspaces(user_id=user_id, limit=200)
    for workspace in workspaces:
        if _workspace_contains_path(workspace, repo_abs):
            project = _resolve_workspace_project_for_path(
                db,
                workspace_id=str(workspace.get("id") or ""),
                path=repo_abs,
            ) or _ensure_unassigned_workspace_project(db, workspace)
            return project, workspace

    workspace = db.upsert_workspace(
        {
            "user_id": user_id,
            "name": _auto_project_name(repo_abs),
            "description": f"Workspace auto-created for {repo_abs}",
            "root_path": repo_abs,
            "metadata": {"auto_created": True},
        }
    )
    db.upsert_workspace_mount(
        {
            "workspace_id": workspace["id"],
            "user_id": user_id,
            "mount_path": repo_abs,
            "label": os.path.basename(repo_abs.rstrip(os.sep)) or repo_abs,
            "is_primary": True,
        }
    )
    project = db.upsert_workspace_project(
        {
            "workspace_id": workspace["id"],
            "user_id": user_id,
            "name": "General",
            "description": f"Default project for {repo_abs}",
            "default_runtime": "codex",
            "metadata": {"auto_created": True},
        }
    )
    db.replace_workspace_project_scope_rules(
        project_id=str(project.get("id") or ""),
        user_id=user_id,
        rules=[
            {
                "path_prefix": repo_abs,
                "label": "root",
            }
        ],
    )
    return project, workspace


def _resolve_workspace_for_path(
    db: Any,
    *,
    path: Optional[str],
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = os.path.abspath(os.path.expanduser(raw))
    best: Optional[Dict[str, Any]] = None
    best_len = -1
    for workspace in db.list_workspaces(user_id=_ui_user_id(), limit=500):
        if project_id:
            project = db.get_workspace_project(project_id, user_id=_ui_user_id())
            if not project or str(project.get("workspace_id") or "") != str(workspace.get("id") or ""):
                continue
        mounts = db.list_workspace_mounts(
            workspace_id=str(workspace.get("id") or ""),
            user_id=_ui_user_id(),
        )
        workspace_with_mounts = {**workspace, "mounts": mounts}
        for mount in _workspace_folder_mounts(workspace_with_mounts):
            mount_path = str(mount.get("path") or "").strip()
            if not mount_path:
                continue
            try:
                common = os.path.commonpath([candidate, mount_path])
            except ValueError:
                continue
            if common != mount_path:
                continue
            if len(mount_path) > best_len:
                best = workspace_with_mounts
                best_len = len(mount_path)
    return best


def _session_task_id(runtime_id: str, native_session_id: str) -> str:
    return f"task:{runtime_id}:{native_session_id}"


def _agent_session_id(runtime_id: str, native_session_id: str) -> str:
    return f"session:{runtime_id}:{native_session_id}"


def _mirror_runtime_sessions(db: Any) -> Dict[str, Any]:
    repo = _ui_repo()
    project, default_workspace = _ensure_default_project_workspace(db, repo)
    mirrored: List[Dict[str, Any]] = []

    def mirror_one(
        runtime_id: str,
        native_session_id: str,
        *,
        title: str,
        cwd: Optional[str],
        model: Optional[str],
        state: str,
        rollout_path: Optional[str] = None,
        started_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        permission_mode: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        is_current: bool = False,
    ) -> Dict[str, Any]:
        workspace = _resolve_workspace_for_path(db, path=cwd, project_id=None) or default_workspace
        resolved_project = _resolve_workspace_project_for_path(
            db,
            workspace_id=str(workspace.get("id") or ""),
            path=cwd or repo,
        ) or _ensure_unassigned_workspace_project(db, workspace)
        session_id = _agent_session_id(runtime_id, native_session_id)
        task_id = _session_task_id(runtime_id, native_session_id)
        task_status = "active" if is_current or state == "active" else "paused"
        task = db.upsert_shared_task(
            {
                "id": task_id,
                "user_id": _ui_user_id(),
                "project_id": resolved_project["id"],
                "repo": repo,
                "workspace_id": workspace["id"],
                "folder_path": ".",
                "session_id": session_id,
                "thread_id": native_session_id,
                "runtime_id": runtime_id,
                "native_session_id": native_session_id,
                "title": title,
                "status": task_status,
                "created_by": runtime_id,
                "metadata": {
                    "harness": runtime_id,
                    "project_id": resolved_project["id"],
                    "workspace_id": workspace["id"],
                    "session_id": session_id,
                    "native_session_id": native_session_id,
                    "permission_mode": permission_mode,
                    "is_current": is_current,
                    **(metadata or {}),
                },
            }
        )
        session = db.upsert_agent_session(
            {
                "id": session_id,
                "project_id": resolved_project["id"],
                "workspace_id": workspace["id"],
                "user_id": _ui_user_id(),
                "runtime_id": runtime_id,
                "native_session_id": native_session_id,
                "task_id": task["id"],
                "title": title,
                "state": state,
                "model": model,
                "cwd": cwd,
                "rollout_path": rollout_path,
                "permission_mode": permission_mode,
                "started_at": started_at,
                "updated_at": updated_at or _now_iso(),
                "metadata": metadata or {},
            }
        )
        mirrored.append(session)
        return session

    for thread in _repo_codex_threads(repo, limit=18):
        thread_id = str(thread.get("id") or "").strip()
        if not thread_id:
            continue
        mirror_one(
            "codex",
            thread_id,
            title=str(thread.get("title") or "Untitled Codex session"),
            cwd=str(thread.get("cwd") or repo),
            model=thread.get("model"),
            state="active" if thread.get("isCurrent") else "recent",
            rollout_path=thread.get("rolloutPath"),
            started_at=thread.get("startedAt"),
            updated_at=thread.get("updatedAt"),
            permission_mode="native",
            metadata={
                "messages": thread.get("messages") or [],
                "recent_tools": thread.get("recentTools") or [],
                "plan": thread.get("plan") or [],
                "touched_files": thread.get("touchedFiles") or [],
                "rate_limits": thread.get("rateLimits") or {},
                "updated_at_label": thread.get("updatedAtLabel"),
                "preview": thread.get("preview"),
                "is_current": bool(thread.get("isCurrent")),
            },
            is_current=bool(thread.get("isCurrent")),
        )

    claude_session = _find_claude_session(repo)
    if claude_session:
        native_id = str(claude_session.get("id") or "claude-local")
        mirror_one(
            "claude-code",
            native_id,
            title=str(claude_session.get("title") or "Claude Code session"),
            cwd=str(claude_session.get("cwd") or repo),
            model=claude_session.get("model"),
            state=str(claude_session.get("state") or "recent"),
            started_at=claude_session.get("startedAt"),
            updated_at=claude_session.get("updatedAt"),
            permission_mode="native",
            metadata={
                "version": claude_session.get("version"),
                "entrypoint": claude_session.get("entrypoint"),
                "note": claude_session.get("note"),
                "messages": [],
                "recent_tools": [],
                "plan": [],
                "touched_files": [],
                "rate_limits": {},
            },
            is_current=str(claude_session.get("state") or "") == "active",
        )

    return {"project": project, "workspace": default_workspace, "sessions": mirrored}


def _normalize_runtime(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in {"claude", "claude-code", "claude_code"}:
        return "claude-code"
    if raw == "codex":
        return "codex"
    if raw == "both":
        return "both"
    return None


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        pass
    try:
        ts = float(raw)
    except ValueError:
        return None
    if ts > 1_000_000_000_000:
        ts /= 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _format_ui_clock(value: Any) -> str:
    dt = _coerce_datetime(value)
    if dt is None:
        return time.strftime("%H:%M")
    return dt.astimezone().strftime("%H:%M")


def _iso_or_none(value: Any) -> Optional[str]:
    dt = _coerce_datetime(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _path_matches_repo(candidate: Optional[str], repo: Optional[str]) -> bool:
    if not candidate or not repo:
        return False
    try:
        candidate_abs = os.path.abspath(os.path.expanduser(str(candidate)))
        repo_abs = os.path.abspath(os.path.expanduser(str(repo)))
        return os.path.commonpath([candidate_abs, repo_abs]) == repo_abs
    except Exception:
        return False


def _pid_alive(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


def _task_color(status: Optional[str]) -> str:
    value = str(status or "").strip().lower()
    if value == "active":
        return "green"
    if value == "paused":
        return "indigo"
    if value in {"completed", "closed"}:
        return "orange"
    if value == "abandoned":
        return "rose"
    return "green"


def _shared_task_result_to_ui_message(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    digest = str(result.get("digest") or "").strip()
    if not digest:
        return None
    packet_kind = str(result.get("packet_kind") or "result").strip().lower()
    tool_name = str(result.get("tool_name") or packet_kind or "update").strip()
    result_status = str(result.get("result_status") or "completed").strip().lower()
    created_at = _iso_or_none(result.get("updated_at") or result.get("created_at"))
    if packet_kind == "note" or tool_name == "user_note":
        return {
            "id": str(result.get("id") or f"note:{hash(digest)}"),
            "role": "user",
            "content": digest[:1600],
            "createdAt": created_at,
            "label": "note",
        }
    prefix = tool_name.replace("_", " ").strip()
    if result_status == "in_flight":
        prefix = f"{prefix} · in flight"
    return {
        "id": str(result.get("id") or f"result:{hash(digest)}"),
        "role": "agent",
        "content": f"{prefix}: {digest[:1600]}",
        "createdAt": created_at,
        "label": packet_kind,
    }


def _shared_task_messages(title: str, results: List[Dict[str, Any]], *, task_id: str) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = [
        {
            "id": f"{task_id}:seed",
            "role": "user",
            "content": title,
        }
    ]
    for result in reversed(results):
        message = _shared_task_result_to_ui_message(result)
        if message:
            messages.append(message)
    return messages


def _shared_task_to_ui_task(
    db: Any,
    row: Dict[str, Any],
    *,
    index: int,
    result_limit: int = 3,
) -> Dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    task_id = str(row.get("id") or f"task-{index + 1}")
    title = str(row.get("title") or "(untitled)")
    harness = _normalize_runtime(metadata.get("harness"))
    results = db.list_shared_task_results(shared_task_id=task_id, limit=result_limit)
    messages = _shared_task_messages(title, results, task_id=task_id)
    return {
        "id": task_id,
        "color": _task_color(row.get("status")),
        "title": title,
        "created": _format_ui_clock(row.get("created_at") or row.get("updated_at")),
        "updatedAt": _iso_or_none(row.get("updated_at")),
        "status": str(row.get("status") or "active"),
        "links": metadata.get("links") or [],
        "pos": {"x": 150 + (index % 3) * 260, "y": 180 + (index // 3) * 200},
        "harness": harness,
        "source": str(row.get("created_by") or metadata.get("created_via") or "dhee"),
        "messages": messages,
    }


def _get_shared_task_or_404(db: Any, task_id: str) -> Dict[str, Any]:
    row = db.get_shared_task(task_id, user_id=_ui_user_id())
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return row


def _touch_shared_task(
    db: Any,
    row: Dict[str, Any],
    *,
    status: Optional[str] = None,
    metadata_updates: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    metadata.update(metadata_updates or {})
    return db.upsert_shared_task(
        {
            "id": row.get("id"),
            "user_id": row.get("user_id") or _ui_user_id(),
            "repo": row.get("repo") or _ui_repo(),
            "workspace_id": row.get("workspace_id") or _ui_repo(),
            "folder_path": row.get("folder_path"),
            "title": row.get("title"),
            "status": status or row.get("status") or "active",
            "created_by": row.get("created_by") or "sankhya-ui",
            "metadata": metadata,
        }
    )


def _find_claude_session(repo: str) -> Optional[Dict[str, Any]]:
    root = Path.home() / ".claude" / "sessions"
    if not root.exists():
        return None
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        cwd = str(data.get("cwd") or "")
        if cwd and not _path_matches_repo(cwd, repo):
            continue
        pid = data.get("pid")
        return {
            "id": str(data.get("sessionId") or path.stem),
            "cwd": cwd,
            "pid": pid,
            "startedAt": _iso_or_none(data.get("startedAt")),
            "updatedAt": _iso_or_none(path.stat().st_mtime),
            "state": "active" if _pid_alive(pid) else "stale",
            "version": data.get("version"),
            "entrypoint": data.get("entrypoint"),
        }
    return None


def _find_codex_session(repo: str) -> Optional[Dict[str, Any]]:
    state_db = Path.home() / ".codex" / "state_5.sqlite"
    if not state_db.exists():
        return None
    try:
        conn = sqlite3.connect(str(state_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, cwd, title, updated_at, updated_at_ms,
                   created_at, created_at_ms, model, rollout_path
            FROM threads
            WHERE archived = 0
            ORDER BY COALESCE(updated_at_ms, updated_at) DESC
            LIMIT 50
            """
        ).fetchall()
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    for row in rows:
        data = dict(row)
        cwd = str(data.get("cwd") or "")
        if cwd and not _path_matches_repo(cwd, repo):
            continue
        return {
            "id": str(data.get("id") or ""),
            "cwd": cwd,
            "title": data.get("title"),
            "model": data.get("model"),
            "rolloutPath": data.get("rollout_path"),
            "startedAt": _iso_or_none(
                data.get("created_at_ms") or data.get("created_at")
            ),
            "updatedAt": _iso_or_none(
                data.get("updated_at_ms") or data.get("updated_at")
            ),
            "state": "recent",
            "note": (
                "Codex local state shows the most recent thread for this repo, "
                "but does not expose reliable process liveness."
            ),
        }
    return None


def _latest_claude_limit_event() -> Optional[Dict[str, Any]]:
    root = Path.home() / ".claude" / "telemetry"
    if not root.exists():
        return None
    latest: Optional[Dict[str, Any]] = None
    latest_at: Optional[datetime] = None
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:25]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            if not line.strip() or "threshold" not in line and "rate_limit" not in line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = payload.get("event_data") or {}
            name = str(event.get("event_name") or "").strip().lower()
            if name not in {
                "tengu_cost_threshold_reached",
                "rate_limit_reached",
                "quota_limit_reached",
            }:
                continue
            when = _coerce_datetime(event.get("client_timestamp"))
            if when is None:
                continue
            if latest_at is None or when > latest_at:
                latest_at = when
                latest = {
                    "eventName": name,
                    "model": event.get("model"),
                    "lastHitAt": when.astimezone(timezone.utc).isoformat(),
                }
    return latest


def _claude_limit_status() -> Dict[str, Any]:
    latest = _latest_claude_limit_event()
    return {
        "supported": True,
        "lastHitAt": (latest or {}).get("lastHitAt"),
        "resetAt": None,
        "state": "hit" if latest else "unknown",
        "model": (latest or {}).get("model"),
        "note": (
            "Local Claude telemetry exposes the latest detected threshold event, "
            "but not a reliable reset timestamp."
        ),
    }


def _codex_limit_status() -> Dict[str, Any]:
    return {
        "supported": False,
        "lastHitAt": None,
        "resetAt": None,
        "state": "unknown",
        "note": (
            "Codex local state does not currently expose a reliable limit-hit "
            "or reset timestamp."
        ),
    }


def _runtime_entry(
    *,
    runtime_id: str,
    label: str,
    installed: bool,
    configured: Dict[str, Any],
    session: Optional[Dict[str, Any]],
    limits: Dict[str, Any],
) -> Dict[str, Any]:
    if session and session.get("state") == "active":
        state = "active"
    elif installed:
        state = "ready"
    elif session:
        state = "session-detected"
    else:
        state = "not-configured"
    return {
        "id": runtime_id,
        "label": label,
        "installed": installed,
        "state": state,
        "configured": configured,
        "currentSession": session,
        "limits": limits,
    }


def _get_runtime_status_payload() -> Dict[str, Any]:
    from dhee.harness.install import harness_status

    repo = _ui_repo()
    native = harness_status(harness="all")
    claude_native = dict(native.get("claude_code") or {})
    codex_native = dict(native.get("codex") or {})
    claude_installed = bool(
        claude_native.get("enabled_in_config")
        and claude_native.get("hooks_present")
        and claude_native.get("mcp_registered")
    )
    codex_installed = bool(
        codex_native.get("enabled_in_config")
        and codex_native.get("mcp_registered")
        and codex_native.get("instructions_present")
    )
    return {
        "live": True,
        "repo": repo,
        "runtimes": [
            _runtime_entry(
                runtime_id="claude-code",
                label="Claude Code",
                installed=claude_installed,
                configured=claude_native,
                session=_find_claude_session(repo),
                limits=_claude_limit_status(),
            ),
            _runtime_entry(
                runtime_id="codex",
                label="Codex",
                installed=codex_installed,
                configured=codex_native,
                session=_find_codex_session(repo),
                limits=_codex_limit_status(),
            ),
        ],
    }


def _session_messages_from_agent_session(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    metadata = dict(session.get("metadata") or {})
    messages = metadata.get("messages") or []
    out: List[Dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        out.append(
            {
                "id": f"{session.get('id')}:msg:{index}",
                "role": str(message.get("role") or "agent"),
                "content": content,
                "createdAt": _iso_or_none(message.get("timestamp")),
            }
        )
    if out:
        return out
    note = str(metadata.get("note") or "").strip()
    if note:
        return [
            {
                "id": f"{session.get('id')}:note",
                "role": "agent",
                "content": note,
                "createdAt": _iso_or_none(session.get("updated_at")),
            }
        ]
    return []


def _session_summary(db: Any, session: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(session.get("metadata") or {})
    task = db.get_shared_task(str(session.get("task_id") or ""), user_id=_ui_user_id())
    return {
        "id": str(session.get("id") or ""),
        "nativeSessionId": str(session.get("native_session_id") or ""),
        "projectId": session.get("project_id"),
        "workspaceId": session.get("workspace_id"),
        "taskId": session.get("task_id"),
        "runtime": session.get("runtime_id"),
        "title": session.get("title"),
        "state": session.get("state"),
        "model": session.get("model"),
        "cwd": session.get("cwd"),
        "rolloutPath": session.get("rollout_path"),
        "startedAt": session.get("started_at"),
        "updatedAt": session.get("updated_at"),
        "permissionMode": session.get("permission_mode") or "native",
        "isCurrent": bool(metadata.get("is_current") or str(session.get("state") or "") == "active"),
        "preview": metadata.get("preview") or "",
        "messages": _session_messages_from_agent_session(session),
        "recentTools": list(metadata.get("recent_tools") or []),
        "plan": list(metadata.get("plan") or []),
        "touchedFiles": list(metadata.get("touched_files") or []),
        "rateLimits": dict(metadata.get("rate_limits") or {}),
        "taskStatus": (task or {}).get("status"),
    }


def _workspace_project_sessions(db: Any, project: Dict[str, Any]) -> List[Dict[str, Any]]:
    return db.list_agent_sessions(
        user_id=_ui_user_id(),
        project_id=str(project.get("id") or ""),
        workspace_id=str(project.get("workspace_id") or ""),
        limit=200,
    )


def _workspace_sessions(db: Any, workspace: Dict[str, Any]) -> List[Dict[str, Any]]:
    return db.list_agent_sessions(
        user_id=_ui_user_id(),
        workspace_id=str(workspace.get("id") or ""),
        limit=300,
    )


def _project_summary(db: Any, project: Dict[str, Any]) -> Dict[str, Any]:
    sessions = [
        _session_summary(db, session)
        for session in _workspace_project_sessions(db, project)[:80]
    ]
    scope_rules = [
        {
            "id": str(rule.get("id") or ""),
            "pathPrefix": str(rule.get("path_prefix") or ""),
            "label": str(rule.get("label") or ""),
        }
        for rule in _workspace_project_scope_rules(db, str(project.get("id") or ""))
    ]
    return {
        "id": str(project.get("id") or ""),
        "workspaceId": project.get("workspace_id"),
        "name": project.get("name"),
        "label": project.get("name"),
        "description": project.get("description"),
        "defaultRuntime": project.get("default_runtime") or "codex",
        "color": project.get("color"),
        "icon": project.get("icon"),
        "updatedAt": project.get("updated_at"),
        "scopeRules": scope_rules,
        "sessions": sessions,
    }


def _workspace_summary(db: Any, workspace: Dict[str, Any]) -> Dict[str, Any]:
    mounts = db.list_workspace_mounts(
        workspace_id=str(workspace.get("id") or ""),
        user_id=_ui_user_id(),
    )
    workspace_with_mounts = {**workspace, "mounts": mounts}
    projects = [
        _project_summary(db, project)
        for project in db.list_workspace_projects(
            workspace_id=str(workspace.get("id") or ""),
            user_id=_ui_user_id(),
            limit=100,
        )
    ]
    sessions = [
        _session_summary(db, session)
        for session in _workspace_sessions(db, workspace)[:80]
    ]
    return {
        "id": str(workspace.get("id") or ""),
        "name": workspace.get("name"),
        "label": workspace.get("name"),
        "description": workspace.get("description"),
        "rootPath": _workspace_primary_path(workspace_with_mounts),
        "workspacePath": _workspace_primary_path(workspace_with_mounts),
        "folders": _workspace_folder_mounts(workspace_with_mounts),
        "mounts": _workspace_folder_mounts(workspace_with_mounts),
        "updatedAt": workspace.get("updated_at"),
        "projects": projects,
        "sessions": sessions,
        "sessionCount": len(sessions),
    }


def _file_context_payload(db: Any, source_path: str, *, workspace_id: Optional[str]) -> Dict[str, Any]:
    results = db.list_shared_task_results_for_path(
        user_id=_ui_user_id(),
        workspace_id=workspace_id,
        source_path=source_path,
        limit=12,
    )
    memories = []
    try:
        from dhee.mcp_server import get_memory_instance

        memory = get_memory_instance()
        searched = memory.search(source_path, user_id=_ui_user_id(), limit=5)
        raw = searched.get("results") if isinstance(searched, dict) else searched
        for row in raw or []:
            memories.append(_engram_from_memory(row))
    except Exception:
        memories = []
    return {
        "path": source_path,
        "workspaceId": workspace_id,
        "results": results,
        "memories": memories,
        "summary": str((results[0] or {}).get("digest") or "") if results else "",
    }


def _extract_asset_text(path: str, mime_type: Optional[str] = None) -> str:
    suffix = Path(path).suffix.lower()
    try:
        if suffix in {".txt", ".md", ".rst", ".json", ".csv", ".tsv"}:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception:
                return ""
            reader = PdfReader(path)
            parts = []
            for page in reader.pages:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:
                    continue
            return "\n\n".join(part for part in parts if part).strip()
    except Exception:
        return ""
    return ""


def _asset_context_payload(db: Any, asset_id: str) -> Dict[str, Any]:
    asset = db.get_session_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    artifact = None
    artifact_id = str(asset.get("artifact_id") or "").strip()
    if artifact_id:
        try:
            artifact = db.get_artifact(artifact_id)
        except Exception:
            artifact = None
    top_chunks = []
    if artifact:
        for chunk in (artifact.get("chunks") or [])[:6]:
            top_chunks.append(
                {
                    "chunk_index": chunk.get("chunk_index"),
                    "content": str(chunk.get("content") or "")[:1500],
                }
            )
    summary = ""
    if artifact and (artifact.get("extractions") or []):
        summary = str((artifact["extractions"][0] or {}).get("extracted_text") or "")[:1200]
    return {
        "asset": asset,
        "session": db.get_agent_session(str(asset.get("session_id") or "")),
        "artifact": artifact,
        "summary": summary,
        "chunks": top_chunks,
    }


def _session_detail_payload(db: Any, session_id: str) -> Dict[str, Any]:
    session = db.get_agent_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    task = db.get_shared_task(str(session.get("task_id") or ""), user_id=_ui_user_id())
    results = db.list_shared_task_results(
        shared_task_id=str((task or {}).get("id") or ""),
        limit=40,
    ) if task else []
    assets = db.list_session_assets(session_id=session_id, limit=40)
    summary = _session_summary(db, session)
    files = [
        _file_context_payload(
            db,
            path,
            workspace_id=str(session.get("workspace_id") or "") or None,
        )
        for path in summary.get("touchedFiles") or []
    ]
    workspace = db.get_workspace(str(session.get("workspace_id") or ""), user_id=_ui_user_id())
    project = db.get_workspace_project(str(session.get("project_id") or ""), user_id=_ui_user_id())
    line_messages = db.list_workspace_line_messages(
        workspace_id=str(session.get("workspace_id") or ""),
        user_id=_ui_user_id(),
        project_id=str(session.get("project_id") or "") or None,
        limit=20,
    )
    return {
        "live": True,
        "project": _project_summary(db, project) if project else None,
        "workspace": _workspace_summary(db, workspace) if workspace else None,
        "session": summary,
        "task": _shared_task_to_ui_task(db, task, index=0, result_limit=12) if task else None,
        "results": results,
        "assets": assets,
        "files": files,
        "line": {
            "messages": line_messages,
        },
        "runtime": _get_runtime_status_payload(),
    }


def _workspace_detail_payload(db: Any, workspace_id: str) -> Dict[str, Any]:
    workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    sessions = [_session_summary(db, session) for session in _workspace_sessions(db, workspace)[:120]]
    projects = [
        _project_summary(db, project)
        for project in db.list_workspace_projects(
            workspace_id=workspace_id,
            user_id=_ui_user_id(),
            limit=100,
        )
    ]
    return {
        "live": True,
        "workspace": _workspace_summary(db, workspace),
        "projects": projects,
        "sessions": sessions,
        "line": {
            "messages": db.list_workspace_line_messages(
                workspace_id=workspace_id,
                user_id=_ui_user_id(),
                limit=40,
            )
        },
        "runtime": _get_runtime_status_payload(),
    }


def _build_project_index_payload() -> Dict[str, Any]:
    db = _get_db()
    sync = _mirror_runtime_sessions(db)
    workspaces = [
        _workspace_summary(db, workspace)
        for workspace in db.list_workspaces(user_id=_ui_user_id(), limit=100)
    ]
    current_workspace_id = ""
    current_project_id = ""
    current_session_id = ""
    latest_sessions = db.list_agent_sessions(user_id=_ui_user_id(), limit=1)
    if latest_sessions:
        latest = latest_sessions[0] or {}
        current_workspace_id = str(latest.get("workspace_id") or "")
        current_project_id = str(latest.get("project_id") or "")
        current_session_id = str(latest.get("id") or "")
    if not current_workspace_id:
        current_workspace_id = str((sync.get("workspace") or {}).get("id") or "")
    if not current_project_id:
        current_project_id = str((sync.get("project") or {}).get("id") or "")
    if not current_session_id:
        sessions = sync.get("sessions") or []
        if sessions:
            current_session_id = str((sessions[0] or {}).get("id") or "")
    return {
        "live": True,
        "workspaces": workspaces,
        "currentProjectId": current_project_id,
        "currentWorkspaceId": current_workspace_id,
        "currentSessionId": current_session_id,
    }


def _build_project_canvas_payload(project_id: str) -> Dict[str, Any]:
    project = _get_db().get_workspace_project(project_id, user_id=_ui_user_id())
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _build_workspace_canvas_payload(str(project.get("workspace_id") or ""), focus_project_id=project_id)


def _build_workspace_canvas_payload(
    workspace_id: str,
    *,
    focus_project_id: Optional[str] = None,
) -> Dict[str, Any]:
    db = _get_db()
    _mirror_runtime_sessions(db)
    workspace = db.get_workspace(workspace_id, user_id=_ui_user_id())
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_summary = _workspace_summary(db, workspace)
    projects = workspace_summary.get("projects") or []
    current_project_id = str(focus_project_id or "") or str((projects[0] or {}).get("id") or "")
    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    file_nodes: Dict[str, Dict[str, Any]] = {}
    asset_nodes: Dict[str, Dict[str, Any]] = {}
    result_nodes: Dict[str, Dict[str, Any]] = {}
    current_session_id = ""

    def add_node(node: Dict[str, Any]) -> None:
        if not any(str(existing.get("id") or "") == str(node.get("id") or "") for existing in nodes):
            nodes.append(node)

    def add_link(source: str, target: str, label: str, curvature: float = 0.08) -> None:
        link_id = f"{source}->{target}:{label}"
        if any(str(existing.get("id") or "") == link_id for existing in links):
            return
        links.append(
            {
                "id": link_id,
                "source": source,
                "target": target,
                "label": label,
                "curvature": curvature,
            }
        )

    workspace_node_id = f"workspace:{workspace_id}"
    bus_node_id = f"channel:workspace:{workspace_id}"
    add_node(
        {
            "id": workspace_node_id,
            "type": "workspace",
            "label": str(workspace_summary.get("name") or "Workspace"),
            "subLabel": "workspace",
            "body": str(workspace_summary.get("description") or _workspace_primary_path(workspace_summary)),
            "accent": "var(--accent)",
            "val": 36,
            "meta": {"workspaceId": workspace_id},
        }
    )
    add_node(
        {
            "id": bus_node_id,
            "type": "channel",
            "label": "workspace line",
            "subLabel": "shared bus",
            "body": "Broadcasts and shared context visible to every project in this workspace.",
            "accent": "var(--green)",
            "val": 18,
            "meta": {"workspaceId": workspace_id, "channel": "workspace"},
        }
    )
    add_link(workspace_node_id, bus_node_id, "bus", 0.02)

    session_summaries: List[Dict[str, Any]] = []
    task_payload: List[Dict[str, Any]] = []
    for project_index, project in enumerate(projects):
        project_id = str(project.get("id") or "")
        project_node_id = f"project:{project_id}"
        channel_node_id = f"channel:project:{project_id}"
        add_node(
            {
                "id": project_node_id,
                "type": "project",
                "label": str(project.get("name") or "Project"),
                "subLabel": f"default {project.get('defaultRuntime') or 'codex'}",
                "body": str(project.get("description") or "Logical stream inside the workspace."),
                "accent": str(project.get("color") or "var(--indigo)"),
                "val": 24,
                "meta": {"workspaceId": workspace_id, "projectId": project_id},
            }
        )
        add_node(
            {
                "id": channel_node_id,
                "type": "channel",
                "label": f"{project.get('name') or 'Project'} channel",
                "subLabel": "project channel",
                "body": "Project-local collaboration line for runtime broadcasts and suggested work.",
                "accent": "var(--green)",
                "val": 12,
                "meta": {"workspaceId": workspace_id, "projectId": project_id, "channel": "project"},
            }
        )
        add_link(workspace_node_id, project_node_id, "project", 0.04 + project_index * 0.01)
        add_link(project_node_id, channel_node_id, "channel", 0.06)

        for session in project.get("sessions") or []:
            session_id = str(session.get("id") or "")
            if not current_session_id:
                current_session_id = session_id
            session_summaries.append(session)
            add_node(
                {
                    "id": session_id,
                    "type": "session",
                    "label": str(session.get("title") or "Session"),
                    "subLabel": f"{session.get('runtime')} · {session.get('model') or 'unknown'}",
                    "body": str(session.get("preview") or "Mirrored native session."),
                    "accent": "var(--green)" if session.get("state") == "active" else "var(--accent)",
                    "status": session.get("state"),
                    "val": 10,
                    "meta": {
                        "workspaceId": workspace_id,
                        "projectId": project_id,
                        "taskId": session.get("taskId"),
                        "permissionMode": session.get("permissionMode"),
                    },
                }
            )
            add_link(project_node_id, session_id, "session", 0.08)
            add_link(channel_node_id, session_id, "reads", 0.14)
            for path in session.get("touchedFiles") or []:
                if path not in file_nodes:
                    file_nodes[path] = {
                        "id": path,
                        "type": "file",
                        "label": os.path.basename(path) or path,
                        "subLabel": _repo_relative_path(path, _ui_repo()),
                        "body": "Shared file context",
                        "accent": "var(--indigo)",
                        "val": 8,
                        "meta": {"workspaceId": workspace_id, "path": path},
                    }
                add_link(session_id, path, "touched", 0.12)
            for asset in db.list_session_assets(session_id=session_id, limit=20):
                asset_id = str(asset.get("id") or "")
                if asset_id not in asset_nodes:
                    asset_nodes[asset_id] = {
                        "id": asset_id,
                        "type": "asset",
                        "label": str(asset.get("name") or "asset"),
                        "subLabel": str(asset.get("mime_type") or "file"),
                        "body": "Reusable workspace asset",
                        "accent": "var(--rose)",
                        "val": 8,
                        "meta": {"assetId": asset_id, "workspaceId": workspace_id},
                    }
                add_link(session_id, asset_id, "asset", 0.18)

        task_rows = db.list_shared_tasks(
            user_id=_ui_user_id(),
            workspace_id=workspace_id,
            project_id=project_id,
            limit=100,
        )
        for task_index, row in enumerate(task_rows):
            if not isinstance(row, dict):
                continue
            task_ui = _shared_task_to_ui_task(db, row, index=task_index, result_limit=12)
            task_payload.append(task_ui)
            task_id = str(task_ui.get("id") or "")
            add_node(
                {
                    "id": task_id,
                    "type": "task",
                    "label": str(task_ui.get("title") or "Task"),
                    "subLabel": str(row.get("created_by") or "dhee"),
                    "body": (
                        task_ui["messages"][-1]["content"]
                        if task_ui.get("messages")
                        else "Shared task context"
                    ),
                    "accent": "var(--green)" if str(row.get("status") or "") == "active" else "var(--accent)",
                    "status": row.get("status"),
                    "val": 10,
                    "meta": {
                        "workspaceId": workspace_id,
                        "projectId": project_id,
                        "taskId": task_id,
                    },
                }
            )
            add_link(project_node_id, task_id, "task", 0.16)
            session_id = str(row.get("session_id") or "")
            if session_id:
                add_link(session_id, task_id, "owns", 0.12)
            for result in db.list_shared_task_results(shared_task_id=task_id, limit=24):
                result_id = str(result.get("id") or "")
                if result_id not in result_nodes:
                    result_nodes[result_id] = {
                        "id": result_id,
                        "type": "result",
                        "label": str(result.get("tool_name") or "result"),
                        "subLabel": str(result.get("packet_kind") or "digest"),
                        "body": str(result.get("digest") or "Shared result"),
                        "accent": "var(--green)",
                        "val": 6,
                        "meta": {
                            "workspaceId": workspace_id,
                            "projectId": project_id,
                            "taskId": task_id,
                            "resultId": result_id,
                        },
                    }
                add_link(task_id, result_id, "result", 0.22)
                source_path = str(result.get("source_path") or "").strip()
                if source_path:
                    if source_path not in file_nodes:
                        file_nodes[source_path] = {
                            "id": source_path,
                            "type": "file",
                            "label": os.path.basename(source_path) or source_path,
                            "subLabel": _repo_relative_path(source_path, _ui_repo()),
                            "body": "Shared file context",
                            "accent": "var(--indigo)",
                            "val": 8,
                            "meta": {"workspaceId": workspace_id, "path": source_path},
                        }
                    add_link(result_id, source_path, "touches", 0.18)

    for node in file_nodes.values():
        add_node(node)
    for node in asset_nodes.values():
        add_node(node)
    for node in result_nodes.values():
        add_node(node)

    line_messages = db.list_workspace_line_messages(
        workspace_id=workspace_id,
        user_id=_ui_user_id(),
        limit=120,
    )
    for message in line_messages:
        message_id = str(message.get("id") or "")
        source_project_id = str(message.get("project_id") or "")
        target_project_id = str(message.get("target_project_id") or "")
        message_title = str(message.get("title") or "").strip()
        body = str(message.get("body") or "").strip()
        add_node(
            {
                "id": message_id,
                "type": "broadcast",
                "label": message_title or (body[:48] + ("…" if len(body) > 48 else "")) or "Broadcast",
                "subLabel": f"{message.get('message_kind') or 'update'} · {message.get('channel') or 'workspace'}",
                "body": body,
                "accent": "var(--accent)",
                "val": 7,
                "meta": {
                    "workspaceId": workspace_id,
                    "projectId": source_project_id or None,
                    "targetProjectId": target_project_id or None,
                    "taskId": message.get("task_id"),
                    "sessionId": message.get("session_id"),
                },
            }
        )
        if source_project_id:
            add_link(f"channel:project:{source_project_id}", message_id, "broadcast", 0.3)
        else:
            add_link(bus_node_id, message_id, "broadcast", 0.3)
        if target_project_id:
            add_link(message_id, f"project:{target_project_id}", "targets", 0.36)
        if message.get("task_id"):
            add_link(message_id, str(message.get("task_id") or ""), "suggests", 0.4)

    return {
        "live": True,
        "repo": _ui_repo(),
        "workspace": workspace_summary,
        "graph": {"nodes": nodes, "links": links},
        "sessions": session_summaries,
        "tasks": task_payload,
        "files": list(file_nodes.values()),
        "currentSessionId": current_session_id,
        "currentProjectId": current_project_id,
        "currentWorkspaceId": workspace_id,
        "runtime": _get_runtime_status_payload(),
        "line": {"messages": line_messages},
    }


def _build_workspace_graph_payload() -> Dict[str, Any]:
    index = _build_project_index_payload()
    workspace_id = str(index.get("currentWorkspaceId") or "")
    canvas = _build_workspace_canvas_payload(
        workspace_id,
        focus_project_id=str(index.get("currentProjectId") or "") or None,
    )
    return {
        **canvas,
        "workspaces": index.get("workspaces") or [],
    }


def _line_cursor(message: Optional[Dict[str, Any]]) -> str:
    if not message:
        return ""
    return f"{str(message.get('created_at') or '')}|{str(message.get('id') or '')}"


def _create_suggested_task_from_broadcast(
    db: Any,
    *,
    workspace_id: str,
    project_id: str,
    source_project_id: Optional[str],
    title: str,
    body: str,
    session_id: Optional[str],
) -> Dict[str, Any]:
    task = db.upsert_shared_task(
        {
            "user_id": _ui_user_id(),
            "project_id": project_id,
            "repo": _ui_repo(),
            "workspace_id": workspace_id,
            "folder_path": ".",
            "session_id": session_id,
            "runtime_id": "workspace-line",
            "title": title,
            "status": "paused",
            "created_by": "workspace-line",
            "metadata": {
                "suggested": True,
                "source_project_id": source_project_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
            },
        }
    )
    db.save_shared_task_result(
        {
            "shared_task_id": task.get("id"),
            "result_key": f"broadcast:{workspace_id}:{project_id}:{int(time.time() * 1000)}",
            "project_id": project_id,
            "workspace_id": workspace_id,
            "repo": _ui_repo(),
            "packet_kind": "broadcast",
            "tool_name": "workspace-line",
            "result_status": "completed",
            "digest": body,
            "metadata": {
                "source_project_id": source_project_id,
                "workspace_id": workspace_id,
            },
        }
    )
    return task



def _repo_codex_threads(repo: str, limit: int = 6) -> List[Dict[str, Any]]:
    state_db = Path.home() / ".codex" / "state_5.sqlite"
    if not state_db.exists():
        return []
    conn = sqlite3.connect(str(state_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, cwd, title, model, rollout_path, updated_at, updated_at_ms,
                   created_at, created_at_ms
            FROM threads
            WHERE archived = 0 AND cwd LIKE ?
            ORDER BY COALESCE(updated_at_ms, updated_at) DESC
            LIMIT ?
            """,
            (f"{repo}%", limit),
        ).fetchall()
    finally:
        conn.close()
    items: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        data = dict(row)
        rollout_path = str(data.get("rollout_path") or "")
        rollout = _parse_codex_rollout(Path(rollout_path), repo=repo)
        updated_at = _iso_or_none(data.get("updated_at_ms") or data.get("updated_at"))
        items.append(
            {
                "id": str(data.get("id") or f"thread-{index}"),
                "title": str(data.get("title") or "Untitled Codex session"),
                "cwd": str(data.get("cwd") or repo),
                "model": data.get("model"),
                "updatedAt": updated_at,
                "updatedAtLabel": _format_ui_clock(updated_at),
                "rolloutPath": rollout_path,
                "isCurrent": index == 0,
                **rollout,
            }
        )
    return items


def _parse_codex_rollout(path: Path, *, repo: str) -> Dict[str, Any]:
    if not path.exists():
        return {
            "preview": "",
            "messages": [],
            "recentTools": [],
            "plan": [],
            "touchedFiles": [],
            "rateLimits": {},
            "tokenUsage": {},
            "lastTokenUsage": {},
            "contextWindow": None,
        }
    messages: deque[Dict[str, Any]] = deque(maxlen=8)
    recent_tools: deque[str] = deque(maxlen=8)
    touched_files: set[str] = set()
    latest_plan: List[Dict[str, Any]] = []
    latest_rate_limits: Dict[str, Any] = {}
    latest_token_usage: Dict[str, Any] = {}
    latest_last_token_usage: Dict[str, Any] = {}
    context_window: Optional[int] = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = item.get("type")
            payload = item.get("payload") or {}
            timestamp = item.get("timestamp")
            if kind == "response_item":
                payload_type = payload.get("type")
                if payload_type == "message":
                    text = _rollout_message_text(payload)
                    if text:
                        messages.append(
                            {
                                "role": payload.get("role") or "assistant",
                                "content": text,
                                "timestamp": timestamp,
                            }
                        )
                elif payload_type == "function_call":
                    name = str(payload.get("name") or "").strip()
                    if name:
                        recent_tools.append(name)
                    arguments = str(payload.get("arguments") or "")
                    touched_files.update(_extract_repo_paths(arguments, repo))
                    if name == "update_plan":
                        latest_plan = _parse_update_plan(arguments)
                    if name == "apply_patch":
                        touched_files.update(_extract_patch_paths(arguments, repo))
            elif kind == "event_msg":
                event_type = payload.get("type")
                if event_type == "agent_message":
                    message = str(payload.get("message") or "").strip()
                    if message:
                        messages.append(
                            {
                                "role": "assistant",
                                "content": message,
                                "timestamp": timestamp,
                            }
                        )
                elif event_type == "token_count":
                    latest_rate_limits = dict(payload.get("rate_limits") or {})
                    info = payload.get("info") or {}
                    latest_token_usage = dict(info.get("total_token_usage") or {})
                    latest_last_token_usage = dict(info.get("last_token_usage") or {})
                    if info.get("model_context_window") is not None:
                        try:
                            context_window = int(info.get("model_context_window"))
                        except (TypeError, ValueError):
                            context_window = None
    preview = ""
    if messages:
        for candidate in reversed(messages):
            if candidate.get("role") == "assistant":
                preview = str(candidate.get("content") or "")
                break
        if not preview:
            preview = str(messages[-1].get("content") or "")
    return {
        "preview": preview[:260],
        "messages": list(messages),
        "recentTools": list(recent_tools),
        "plan": latest_plan,
        "touchedFiles": sorted(touched_files)[:10],
        "rateLimits": latest_rate_limits,
        "tokenUsage": latest_token_usage,
        "lastTokenUsage": latest_last_token_usage,
        "contextWindow": context_window,
    }


def _router_codex_native_usage(repo: str) -> Dict[str, Any]:
    threads = _repo_codex_threads(repo, limit=3)
    if not threads:
        return {"available": False}
    current = next((thread for thread in threads if thread.get("isCurrent")), threads[0])
    total = dict(current.get("tokenUsage") or {})
    last = dict(current.get("lastTokenUsage") or {})
    rate_limits = dict(current.get("rateLimits") or {})
    primary = dict(rate_limits.get("primary") or {})
    secondary = dict(rate_limits.get("secondary") or {})

    def _coerce_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _coerce_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _epoch_to_iso(value: Any) -> Optional[str]:
        try:
            if value in (None, ""):
                return None
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            return None

    return {
        "available": bool(total or last or rate_limits),
        "threadId": current.get("id"),
        "title": current.get("title"),
        "model": current.get("model"),
        "updatedAt": current.get("updatedAt"),
        "totalTokens": _coerce_int(total.get("total_tokens")),
        "inputTokens": _coerce_int(total.get("input_tokens")),
        "cachedInputTokens": _coerce_int(total.get("cached_input_tokens")),
        "outputTokens": _coerce_int(total.get("output_tokens")),
        "reasoningOutputTokens": _coerce_int(total.get("reasoning_output_tokens")),
        "lastTurnTokens": _coerce_int(last.get("total_tokens")),
        "lastTurnInputTokens": _coerce_int(last.get("input_tokens")),
        "lastTurnCachedInputTokens": _coerce_int(last.get("cached_input_tokens")),
        "lastTurnOutputTokens": _coerce_int(last.get("output_tokens")),
        "contextWindow": current.get("contextWindow"),
        "primaryUsedPercent": _coerce_float(primary.get("used_percent")),
        "secondaryUsedPercent": _coerce_float(secondary.get("used_percent")),
        "resetAt": _epoch_to_iso(primary.get("resets_at")),
        "secondaryResetAt": _epoch_to_iso(secondary.get("resets_at")),
        "rateLimits": rate_limits,
    }


def _rollout_message_text(payload: Dict[str, Any]) -> str:
    content = payload.get("content") or []
    parts: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _parse_update_plan(arguments: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(arguments)
    except Exception:
        return []
    items = []
    for row in data.get("plan") or []:
        if not isinstance(row, dict):
            continue
        step = str(row.get("step") or "").strip()
        if not step:
            continue
        items.append(
            {
                "step": step,
                "status": str(row.get("status") or "pending"),
            }
        )
    return items[:8]


def _extract_repo_paths(text: str, repo: str) -> List[str]:
    matches = re.findall(r"/Users/[^\s\"']+", text or "")
    out: List[str] = []
    for raw in matches:
        candidate = raw.rstrip(",:;)]}")
        if candidate.startswith(repo):
            out.append(candidate)
    return out


def _extract_patch_paths(text: str, repo: str) -> List[str]:
    paths = []
    for match in re.findall(r"\*\*\* (?:Add|Update|Delete) File: (.+)", text or ""):
        candidate = str(match).strip()
        if candidate.startswith(repo):
            paths.append(candidate)
    return paths


def _repo_relative_path(path: str, repo: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(repo).resolve()))
    except Exception:
        return path


def _task_preview(task: Dict[str, Any]) -> str:
    messages = list(task.get("messages") or [])
    for message in reversed(messages):
        content = str((message or {}).get("content") or "").strip()
        if content and content != task.get("title"):
            return content[:220]
    return "No shared outputs yet."


def _task_color_value(status: Optional[str]) -> str:
    color = _task_color(status)
    mapping = {
        "green": "var(--green)",
        "indigo": "var(--indigo)",
        "orange": "var(--accent)",
        "rose": "var(--rose)",
    }
    return mapping.get(color, "var(--ink)")


def _dhee_data_dir_str() -> str:
    try:
        from dhee.configs.base import _dhee_data_dir

        return str(_dhee_data_dir())
    except Exception:
        return str(Path.home() / ".dhee")


def _default_confidence_groups() -> List[Dict[str, Any]]:
    return [
        {"group": "source_code", "confidence": 0.0, "trend": "stable"},
        {"group": "test", "confidence": 0.0, "trend": "stable"},
        {"group": "data", "confidence": 0.0, "trend": "stable"},
        {"group": "doc", "confidence": 0.0, "trend": "stable"},
    ]


def _seven_day_savings(agent_id: Optional[str] = None) -> List[int]:
    try:
        from dhee.router import ptr_store, stats as rstats

        root = ptr_store._root()
        if not root.exists():
            return [0] * 7
        now = time.time()
        buckets = [0] * 7
        selected_agent = None if agent_id in (None, "", "all") else rstats._normalize_agent_id(agent_id)
        for session_dir in root.iterdir():
            if not session_dir.is_dir():
                continue
            for meta_file in session_dir.glob("*.json"):
                try:
                    mtime = meta_file.stat().st_mtime
                except OSError:
                    continue
                age_days = int((now - mtime) // 86400)
                if 0 <= age_days < 7:
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    meta_agent = rstats._agent_from_meta(meta)
                    if selected_agent and meta_agent != selected_agent:
                        continue
                    chars = meta.get("char_count") or 0
                    if not chars:
                        chars = int(meta.get("stdout_bytes", 0) or 0) + int(
                            meta.get("stderr_bytes", 0) or 0
                        )
                    buckets[6 - age_days] += int(chars / 3.5)
        return buckets
    except Exception:
        return [0] * 7


def _seven_day_labels() -> List[str]:
    names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    today = time.localtime().tm_wday  # Mon=0..Sun=6
    today_idx = (today + 1) % 7  # Sun=0..Sat=6
    out = []
    for i in range(6, -1, -1):
        out.append(names[(today_idx - i) % 7])
    out[-1] = "Now"
    return out


def _load_evolution_events() -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    try:
        from dhee.configs.base import _dhee_data_dir

        log_dir = Path(_dhee_data_dir()) / "evolution"
    except Exception:
        log_dir = Path.home() / ".dhee" / "evolution"
    if not log_dir.exists():
        return []
    for f in sorted(log_dir.glob("*.jsonl"))[-3:]:
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(
                    {
                        "id": str(ev.get("id") or f"ev-{len(events)+1}"),
                        "time": ev.get("time")
                        or time.strftime(
                            "%H:%M", time.localtime(ev.get("ts", time.time()))
                        ),
                        "type": ev.get("type", "tune"),
                        "label": ev.get("label", "event"),
                        "detail": ev.get("detail", ""),
                        "impact": ev.get("impact", "neutral"),
                    }
                )
        except OSError:
            continue
    return events[-40:][::-1]


# default app for `uvicorn dhee.ui.server:app`
app = create_app()
