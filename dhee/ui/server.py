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
import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

_LOCAL_UI_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?"
_SESSION_LOG_TAIL_BYTES = int(os.environ.get("DHEE_UI_SESSION_LOG_TAIL_BYTES", str(768 * 1024)))
_RUNTIME_MIRROR_CODEX_LIMIT = int(os.environ.get("DHEE_UI_RUNTIME_CODEX_LIMIT", "6"))
_RUNTIME_MIRROR_CLAUDE_LIMIT = int(os.environ.get("DHEE_UI_RUNTIME_CLAUDE_LIMIT", "6"))
_SESSION_LOG_PARSE_CACHE: Dict[Tuple[str, str, str], Tuple[int, int, Dict[str, Any]]] = {}
_MIRROR_RUNTIME_CACHE_TTL_SECONDS = float(os.environ.get("DHEE_UI_MIRROR_RUNTIME_TTL_SECONDS", "3"))
_MIRROR_RUNTIME_CACHE: Dict[Tuple[str, ...], Tuple[float, Dict[str, Any]]] = {}
_MIRROR_RUNTIME_LOCK = threading.Lock()
_UI_DB = None


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


class UiPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class WorkspaceRootCreatePayload(UiPayload):
    name: Optional[str] = Field(default=None, validation_alias=AliasChoices("name", "label"))
    description: Optional[str] = None
    root_path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("root_path", "rootPath", "workspace_path", "workspacePath", "path"),
    )


class WorkspaceRootUpdatePayload(UiPayload):
    name: Optional[str] = Field(default=None, validation_alias=AliasChoices("name", "label"))
    description: Optional[str] = None


class WorkspaceCreatePayload(UiPayload):
    workspace_path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workspace_path", "workspacePath", "root_path", "rootPath", "path"),
    )
    label: Optional[str] = None
    folder_path: Optional[str] = Field(default=None, validation_alias=AliasChoices("folder_path", "folderPath"))
    is_primary: bool = Field(default=False, validation_alias=AliasChoices("is_primary", "isPrimary"))
    folders: Optional[List[str]] = None


class WorkspaceFolderPayload(UiPayload):
    path: Optional[str] = Field(default=None, validation_alias=AliasChoices("path", "mount_path", "mountPath", "rootPath"))
    label: Optional[str] = None


class WorkspaceUpdatePayload(UiPayload):
    label: Optional[str] = Field(default=None, validation_alias=AliasChoices("label", "name"))
    description: Optional[str] = None
    root_path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("root_path", "rootPath", "workspace_path", "workspacePath", "path"),
    )


class WorkspaceProjectCreatePayload(UiPayload):
    name: Optional[str] = Field(default=None, validation_alias=AliasChoices("name", "label"))
    description: Optional[str] = None
    default_runtime: Optional[str] = Field(default=None, validation_alias=AliasChoices("default_runtime", "defaultRuntime"))
    color: Optional[str] = None
    icon: Optional[str] = None
    scope_rules: Optional[List[Dict[str, Any]]] = Field(default=None, validation_alias=AliasChoices("scope_rules", "scopeRules"))


class WorkspaceProjectUpdatePayload(UiPayload):
    name: Optional[str] = Field(default=None, validation_alias=AliasChoices("name", "label"))
    description: Optional[str] = None
    default_runtime: Optional[str] = Field(default=None, validation_alias=AliasChoices("default_runtime", "defaultRuntime"))
    color: Optional[str] = None
    icon: Optional[str] = None
    scope_rules: Optional[List[Dict[str, Any]]] = Field(default=None, validation_alias=AliasChoices("scope_rules", "scopeRules"))


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


class ContextUpsertPayload(BaseModel):
    context_id: Optional[str] = None
    expected_content_hash: Optional[str] = None
    title: str
    content: str
    scope: str
    kind: str = "note"
    project_id: Optional[str] = None
    team_id: Optional[str] = None
    user_id: Optional[str] = None
    tags: Optional[List[str]] = None
    summary: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ProposalCreatePayload(BaseModel):
    title: str
    content: str
    scope: str
    kind: str
    project_id: Optional[str] = None
    team_id: Optional[str] = None
    proposed_by_user_id: str
    supersedes_id: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class ProposalDecisionPayload(BaseModel):
    reviewer_user_id: str


class FindingResolvePayload(BaseModel):
    resolved_by: Optional[str] = None


class IntegrationPayload(BaseModel):
    scope: str
    target_id: str
    type: str
    value: Any
    metadata: Optional[Dict[str, Any]] = None


class TeamJoinPayload(BaseModel):
    org_id: str
    project_id: Optional[str] = None
    team_id: Optional[str] = None
    role: Optional[str] = "developer"
    repo_root: Optional[str] = None


class EnterpriseWorkspacePayload(BaseModel):
    name: str
    root_path: Optional[str] = None
    default_branch: Optional[str] = "main"


class EnterpriseProjectCreatePayload(BaseModel):
    name: str
    project_id: Optional[str] = None
    description: Optional[str] = ""


class ProjectTeamCreatePayload(BaseModel):
    name: str
    team_id: Optional[str] = None
    description: Optional[str] = ""


class ProjectFolderAddPayload(BaseModel):
    local_path: Optional[str] = None
    repo_url: Optional[str] = None
    label: Optional[str] = None
    kind: Optional[str] = "folder"


class LocalContextFolderPayload(BaseModel):
    path: str
    shared: Optional[bool] = True


class TeamCollaborationPayload(BaseModel):
    target_team_id: str


# Module-level payloads for the new workspace + context endpoints.
# These need to live outside ``create_app()`` because FastAPI's body
# resolution can't see classes that live in nested function scope when
# ``from __future__ import annotations`` defers annotation evaluation
# (the existing pattern in this file is module-level for body params,
# inline only for ad-hoc query helpers).


class LocalWorkspaceCreatePayload(BaseModel):
    name: Optional[str] = None
    id: Optional[str] = None


class ContextPromotePayload(BaseModel):
    memory_id: str
    repo: Optional[str] = None
    kind: Optional[str] = "learning"
    title: Optional[str] = None


class ContextDemotePayload(BaseModel):
    entry_id: str
    repo: Optional[str] = None


class UiLearningDecisionPayload(BaseModel):
    scope: Optional[str] = "personal"
    repo: Optional[str] = None
    approved_by: Optional[str] = "dhee-ui"
    reason: Optional[str] = None


class UiPortabilityExportPayload(BaseModel):
    output_path: Optional[str] = None
    user_id: str = "default"
    repo: Optional[str] = None


class UiPortabilityImportPayload(BaseModel):
    input_path: str
    user_id: str = "default"
    repo: Optional[str] = None


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


_AUTO_MEMORY_TIER_BY_TYPE = {
    "user": "high",
    "feedback": "high",
    "project": "medium",
    "reference": "medium",
}


def _parse_auto_memory_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    name = path.stem
    description = ""
    mem_type = "project"
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            header = text[3:end]
            body = text[end + 4 :].lstrip("\n")
            for line in header.splitlines():
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                key = key.strip().lower()
                value = value.strip().strip('"').strip("'")
                if key == "name" and value:
                    name = value
                elif key == "description" and value:
                    description = value
                elif key == "type" and value:
                    mem_type = value
    body = body.strip()
    if not body and not description:
        return None
    content_parts: List[str] = [f"[{mem_type}] {name}"]
    if description:
        content_parts.append(description)
    if body:
        content_parts.append(body)
    content = "\n\n".join(content_parts)
    try:
        created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    except OSError:
        created = time.strftime("%Y-%m-%d")
    tier = _AUTO_MEMORY_TIER_BY_TYPE.get(mem_type, "medium")
    file_id = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"auto:{file_id}",
        "tier": tier,
        "content": content,
        "source": "claude_auto_memory",
        "created": created,
        "tags": [mem_type, "auto-memory"],
        "decay": 1.0,
        "reaffirmed": 0,
        "tokens": _estimate_tokens(content),
    }


def _auto_memory_engrams() -> List[Dict[str, Any]]:
    roots = [Path.home() / ".claude" / "projects"]
    engrams: List[Dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            project_dirs = [p for p in root.iterdir() if p.is_dir()]
        except OSError:
            continue
        for project_dir in project_dirs:
            memory_dir = project_dir / "memory"
            if not memory_dir.is_dir():
                continue
            try:
                files = [p for p in memory_dir.iterdir() if p.is_file() and p.suffix == ".md"]
            except OSError:
                continue
            for path in files:
                if path.name.upper() == "MEMORY.MD":
                    continue
                eng = _parse_auto_memory_file(path)
                if eng:
                    engrams.append(eng)
    engrams.sort(key=lambda e: e.get("created") or "", reverse=True)
    return engrams


# ─── App factory ──────────────────────────────────────────────────────────────


def create_app(*, serve_static: bool = True, dev_mode: bool = False) -> FastAPI:
    app = FastAPI(title="Sankhya — Dhee UI", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=os.environ.get("DHEE_UI_CORS_ORIGIN_REGEX") or _LOCAL_UI_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─── Memory ──────────────────────────────────────────────────────────────

    def _get_memory():
        from dhee.cli_config import get_memory_instance

        return get_memory_instance(None)

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
            engrams.extend(_auto_memory_engrams())
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
            enterprise_saved_tokens = 0
            enterprise_saved_pct = 0.0
            enterprise_raw_tokens = 0
            enterprise_summary_tokens = 0
            enterprise_raw_fallbacks = 0
            enterprise_gate_suggestions = 0
            enterprise_gate_denials = 0
            enterprise_gate_fallbacks = 0
            return {
                "live": True,
                "selectedAgent": selected_agent or "all",
                "sessionTokensSaved": s.get("est_tokens_diverted", 0),
                "enterpriseSavedTokens": enterprise_saved_tokens,
                "enterpriseSavedPct": enterprise_saved_pct,
                "enterpriseRawTokens": enterprise_raw_tokens,
                "enterpriseSummaryTokens": enterprise_summary_tokens,
                "enterpriseRawFallbacks": enterprise_raw_fallbacks,
                "enterpriseGateSuggestions": enterprise_gate_suggestions,
                "enterpriseGateDenials": enterprise_gate_denials,
                "enterpriseGateFallbacks": enterprise_gate_fallbacks,
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
            mem = _get_memory()
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
            mem = _get_memory()
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
            workspace_name = _display_name(payload.name, fallback="Workspace")
            if not workspace_name:
                raise HTTPException(status_code=400, detail="Workspace name is required")
            workspace = db.upsert_workspace(
                {
                    "user_id": _ui_user_id(),
                    "name": workspace_name,
                    "description": payload.description,
                    "root_path": None,
                    "metadata": {"created_via": "sankhya-ui"},
                }
            )
            _mirror_runtime_sessions(db)
            return {"ok": True, "workspace": _workspace_summary(db, workspace)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/legacy-projects")
    def create_project_api(payload: WorkspaceRootCreatePayload) -> Dict[str, Any]:
        try:
            db = _get_db()
            workspace_name = _display_name(payload.name, fallback_path=payload.root_path)
            workspace = db.upsert_workspace(
                {
                    "user_id": _ui_user_id(),
                    "name": workspace_name,
                    "description": payload.description,
                    "root_path": _abs_user_path(payload.root_path) or None,
                    "metadata": {"created_via": "sankhya-ui"},
                }
            )
            _mirror_runtime_sessions(db, extra_paths=[_workspace_primary_path(workspace)])
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
            workspace_path = _abs_user_path(payload.workspace_path)
            if not workspace_path:
                raise HTTPException(status_code=400, detail="workspace_path is required")
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
            _mirror_runtime_sessions(db, extra_paths=[workspace_path])
            return {"ok": True, "workspace": _workspace_summary(db, workspace)}
        except HTTPException:
            raise
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
            project_name = _display_name(payload.name, fallback="Project")
            if not project_name:
                raise HTTPException(status_code=400, detail="Project name is required")
            project = db.upsert_workspace_project(
                {
                    "workspace_id": workspace_id,
                    "user_id": _ui_user_id(),
                    "name": project_name,
                    "description": payload.description,
                    "default_runtime": payload.default_runtime or "codex",
                    "color": payload.color,
                    "icon": payload.icon,
                    "metadata": {"created_via": "sankhya-ui"},
                }
            )
            rules = _normalize_scope_rules(payload.scope_rules)
            db.replace_workspace_project_scope_rules(
                project_id=str(project.get("id") or ""),
                user_id=_ui_user_id(),
                rules=rules,
            )
            _mirror_runtime_sessions(
                db,
                extra_paths=[str(rule.get("path_prefix") or "") for rule in rules],
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
            scan_paths: List[str] = []
            if payload.scope_rules is not None:
                rules = _normalize_scope_rules(payload.scope_rules)
                db.replace_workspace_project_scope_rules(
                    project_id=project_id,
                    user_id=_ui_user_id(),
                    rules=rules,
                )
                scan_paths.extend(str(rule.get("path_prefix") or "") for rule in rules)
            workspace = db.get_workspace(str(project.get("workspace_id") or ""), user_id=_ui_user_id())
            if workspace:
                scan_paths.append(_workspace_primary_path(workspace))
            _mirror_runtime_sessions(db, extra_paths=scan_paths)
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
            resolved = _abs_user_path(payload.path)
            if not resolved:
                raise HTTPException(status_code=400, detail="path is required")
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
            _mirror_runtime_sessions(db, extra_paths=[resolved])
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
                _abs_user_path(payload.root_path)
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
                resolved_root = _abs_user_path(next_root)
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
            _mirror_runtime_sessions(db, extra_paths=[next_root])
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
            return {"live": False, "memories": [], "engrams": [], "error": str(exc)}

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
            return {"live": False, "answer": "", "memories": [], "error": str(exc)}

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
            return {"live": False, "context": [], "error": str(exc)}

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
            return {"live": False, "context": [], "error": str(exc)}

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

    # ─── Local context service used by the workspace UI ──────────────────────

    class _LocalContextStore:
        def __init__(self, service: "_LocalContextService") -> None:
            self.service = service

        def list_context_items(self, **kwargs: Any) -> List[Dict[str, Any]]:
            rows = self.service.list_context_items()
            team_id = kwargs.get("team_id")
            project_id = kwargs.get("project_id")
            scope = kwargs.get("scope")
            if team_id:
                rows = [r for r in rows if r.get("team_id") in {team_id, None, ""}]
            if project_id:
                rows = [r for r in rows if r.get("project_id") in {project_id, None, ""}]
            if scope:
                rows = [r for r in rows if r.get("scope") == scope]
            return rows[: int(kwargs.get("limit") or 200)]

        def get_context_item(self, org_id: str, context_id: str) -> Optional[Dict[str, Any]]:
            del org_id
            for row in self.service.list_context_items():
                if str(row.get("context_id") or row.get("id") or "") == context_id:
                    return row
            return None

        def add_context_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
            return self.service.add_context_item(item)

        def list_context_shares(self, **kwargs: Any) -> List[Dict[str, Any]]:
            del kwargs
            return []

        def get_team(self, org_id: str, team_id: str) -> Optional[Dict[str, Any]]:
            del org_id
            return self.service.get_team(team_id)

    class _LocalContextService:
        org_id = "local"

        def __init__(self) -> None:
            self.store = _LocalContextStore(self)

        def close(self) -> None:
            return

        def _state(self) -> Dict[str, Any]:
            return _read_local_context_state()

        def _save(self, state: Dict[str, Any]) -> None:
            _write_local_context_state(state)

        def get_context_manager(self, team_id: str) -> Dict[str, Any]:
            return {
                "manager_id": f"context-manager:{team_id}",
                "owner_user_id": _ui_user_id(),
                "display_name": "Dhee Context Manager",
                "team_id": team_id,
            }

        def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
            state = self._state()
            teams = state.get("teams") or {}
            if team_id in teams:
                return teams[team_id]
            for row in (state.get("folders") or {}).values():
                if isinstance(row, dict) and row.get("team_id") == team_id:
                    return {
                        "team_id": team_id,
                        "name": team_id,
                        "project_id": row.get("project_id") or "local",
                    }
            return None

        def list_context_items(self) -> List[Dict[str, Any]]:
            path = Path(_ui_repo()) / ".dhee" / "context" / "entries.jsonl"
            rows: List[Dict[str, Any]] = []
            if not path.exists():
                return rows
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    cid = str(item.get("context_id") or item.get("id") or "")
                    rows.append({
                        "context_id": cid or hashlib.sha1(line.encode("utf-8")).hexdigest()[:12],
                        "title": item.get("title") or item.get("summary") or "Repo context",
                        "content": item.get("content") or item.get("body") or item.get("summary") or "",
                        "summary": item.get("summary") or item.get("content") or "",
                        "scope": item.get("scope") or "repo",
                        "kind": item.get("kind") or item.get("type") or "note",
                        "project_id": item.get("project_id"),
                        "team_id": item.get("team_id"),
                        "tags": item.get("tags") or [],
                        "metadata": item.get("metadata") or {},
                    })
            except OSError:
                return []
            return rows

        def add_context_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
            context_id = str(item.get("context_id") or f"ctx-{hashlib.sha1(json.dumps(item, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:12]}")
            row = {**item, "context_id": context_id, "id": context_id, "updated_at": _now_iso()}
            path = Path(_ui_repo()) / ".dhee" / "context" / "entries.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
            return row

        def add_context(self, **kwargs: Any) -> Dict[str, Any]:
            return self.add_context_item(kwargs)

        def propose_context(self, **kwargs: Any) -> Dict[str, Any]:
            return self.add_context_item({**kwargs, "status": "proposed"})

        def approve_proposal(self, context_id: str, reviewer_user_id: Optional[str] = None) -> Dict[str, Any]:
            return {"context_id": context_id, "status": "approved", "reviewer_user_id": reviewer_user_id}

        def reject_proposal(self, context_id: str, reviewer_user_id: Optional[str] = None) -> Dict[str, Any]:
            return {"context_id": context_id, "status": "rejected", "reviewer_user_id": reviewer_user_id}

        def inbox_for_viewer(self, **kwargs: Any) -> Dict[str, Any]:
            del kwargs
            return {"proposals": [], "findings": []}

        def resolve_finding(self, finding_id: str, resolved_by: Optional[str] = None) -> Dict[str, Any]:
            return {"finding_id": finding_id, "resolved_by": resolved_by, "status": "resolved"}

        def list_backlinks(self, context_id: str, limit: int = 50) -> List[Dict[str, Any]]:
            del context_id, limit
            return []

        def set_integration(self, **kwargs: Any) -> Dict[str, Any]:
            return {"integration_id": hashlib.sha1(json.dumps(kwargs, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12], **kwargs}

        def create_workspace(self, name: str, root_path: Optional[str] = None, default_branch: str = "main") -> Dict[str, Any]:
            state = self._state()
            ws_id = _ensure_default_workspace(state)
            state["workspaces"][ws_id].update({
                "id": ws_id,
                "name": name or "My Workspace",
                "root_path": _abs_user_path(root_path) or _ui_repo(),
                "default_branch": default_branch,
                "updated_at": _now_iso(),
            })
            self._save(state)
            return state["workspaces"][ws_id]

        def reset_workspace(self) -> Dict[str, int]:
            state = self._state()
            counts = {
                "projects": len(state.get("projects") or {}),
                "teams": len(state.get("teams") or {}),
                "folders": len(state.get("folders") or {}),
            }
            self._save(_empty_state())
            return counts

        def create_project(self, project_id: Optional[str], name: str, description: str = "") -> Dict[str, Any]:
            state = self._state()
            projects = state.setdefault("projects", {})
            pid = project_id or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "local"
            project = {"project_id": pid, "name": name, "description": description, "updated_at": _now_iso()}
            projects[pid] = project
            self._save(state)
            return project

        def delete_project(self, project_id: str) -> Dict[str, Any]:
            state = self._state()
            (state.get("projects") or {}).pop(project_id, None)
            self._save(state)
            return {"project_id": project_id}

        def create_project_team(self, project_id: str, team_id: Optional[str], name: str, description: str = "") -> Dict[str, Any]:
            state = self._state()
            teams = state.setdefault("teams", {})
            tid = team_id or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "local-dev"
            team = {"team_id": tid, "project_id": project_id, "name": name, "description": description, "updated_at": _now_iso()}
            teams[tid] = team
            self._save(state)
            return team

        def _add_folder(self, *, project_id: Optional[str], team_id: Optional[str], local_path: Optional[str], repo_url: Optional[str], label: Optional[str], kind: str) -> Dict[str, Any]:
            state = self._state()
            workspace_id = _ensure_default_workspace(state)
            path = _abs_user_path(local_path) or repo_url or _ui_repo()
            mapping_id = _folder_node_id(path).split(":", 1)[-1]
            row = {
                "mapping_id": mapping_id,
                "path": path,
                "local_path": _abs_user_path(local_path) or None,
                "repo_url": repo_url,
                "label": label or _folder_label(path),
                "kind": kind,
                "project_id": project_id,
                "team_id": team_id,
                "workspace_id": workspace_id,
                "shared": True,
                "linked_at": _now_iso(),
                "source": "ui",
            }
            state.setdefault("folders", {})[path] = row
            self._save(state)
            return {"mapping": row, "folder": row, "mapping_id": mapping_id}

        def add_team_folder(self, team_id: str, local_path: Optional[str] = None, repo_url: Optional[str] = None, label: Optional[str] = None, kind: str = "folder") -> Dict[str, Any]:
            team = self.get_team(team_id) or {}
            return self._add_folder(project_id=team.get("project_id"), team_id=team_id, local_path=local_path, repo_url=repo_url, label=label, kind=kind)

        def add_project_folder(self, project_id: str, local_path: Optional[str] = None, repo_url: Optional[str] = None, label: Optional[str] = None, kind: str = "folder") -> Dict[str, Any]:
            return self._add_folder(project_id=project_id, team_id=None, local_path=local_path, repo_url=repo_url, label=label, kind=kind)

        def remove_project_folder(self, mapping_id: str) -> Dict[str, Any]:
            state = self._state()
            folders = state.get("folders") or {}
            for path, row in list(folders.items()):
                if isinstance(row, dict) and str(row.get("mapping_id") or _folder_node_id(path).split(":", 1)[-1]) == mapping_id:
                    folders.pop(path, None)
                    self._save(state)
                    return {"mapping_id": mapping_id}
            raise ValueError(f"folder mapping not found: {mapping_id}")

        def add_team_collaborator(self, team_id: str, target_team_id: str) -> Dict[str, Any]:
            return {
                "team": self.get_team(team_id) or {"team_id": team_id},
                "target_team": self.get_team(target_team_id) or {"team_id": target_team_id},
                "collaborating_team_ids": [target_team_id],
            }

        def run_ast_extraction(self, project_id: str, team_id: Optional[str] = None) -> Dict[str, Any]:
            state = self._state()
            folders = [
                row for row in (state.get("folders") or {}).values()
                if isinstance(row, dict)
                and (not project_id or row.get("project_id") in {project_id, None})
                and (not team_id or row.get("team_id") == team_id)
            ]
            files_seen = 0
            for row in folders:
                root = _abs_user_path(row.get("local_path") or row.get("path"))
                if not root or not os.path.isdir(root):
                    continue
                for _base, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "__pycache__"}]
                    files_seen += len(files)
                    if files_seen >= 1000:
                        break
            return {
                "project_id": project_id,
                "team_id": team_id,
                "folders_seen": len(folders),
                "files_seen": files_seen,
                "files_extracted": 0,
                "files_cached": files_seen,
                "nodes_upserted": 0,
                "edges_upserted": 0,
                "errors": [],
            }

    def _enterprise_service() -> _LocalContextService:
        return _LocalContextService()

    def _resolve_repo_pointer() -> Dict[str, Any]:
        cwd = Path(os.environ.get("DHEE_UI_REPO") or os.getcwd()).resolve()
        home = Path.home().resolve()
        for candidate in [cwd, *cwd.parents]:
            if candidate == home:
                break
            cfg = candidate / ".dhee" / "config.json"
            if cfg.is_file():
                try:
                    data = json.loads(cfg.read_text(encoding="utf-8"))
                    return {"repo_root": str(candidate), **(data if isinstance(data, dict) else {})}
                except Exception:  # noqa: BLE001
                    return {}
            digest = hashlib.sha1(str(candidate).encode("utf-8")).hexdigest()[:16]
            private_cfg = Path.home() / ".dhee" / "repo_orgs" / f"{digest}.json"
            if private_cfg.is_file():
                try:
                    data = json.loads(private_cfg.read_text(encoding="utf-8"))
                    return data if isinstance(data, dict) else {}
                except Exception:  # noqa: BLE001
                    return {}
        return {}

    @app.get("/api/me")
    def api_me() -> Dict[str, Any]:
        pointer = _resolve_repo_pointer()
        org_id = (
            pointer.get("org_id")
            or os.environ.get("DHEE_UI_ORG_ID")
            or "default"
        )
        user_id = _ui_user_id()
        team_id = pointer.get("team_id")
        team_ids = [team_id] if team_id else []
        # role defaults to developer; manager flag flips when this user is the
        # team's assigned context_manager.owner_user_id.
        role = pointer.get("default_role") or "developer"
        try:
            svc = _enterprise_service()
            try:
                if team_id:
                    mgr = svc.get_context_manager(team_id)
                    if mgr and (
                        mgr.get("owner_user_id") == user_id
                        or mgr.get("manager_id") == user_id
                    ):
                        role = "manager"
            finally:
                svc.close()
        except Exception:  # noqa: BLE001
            pass
        return {
            "live": True,
            "user_id": user_id,
            "org_id": org_id,
            "project_id": pointer.get("project_id"),
            "team_id": team_id,
            "team_ids": team_ids,
            "role": role,
            "repo_root": str(pointer.get("repo_root") or _ui_repo()),
        }

    @app.get("/api/continuity")
    def api_continuity() -> Dict[str, Any]:
        repo = _ui_repo()
        pointer = _resolve_repo_pointer()
        last_session: Optional[Dict[str, Any]] = None
        error = ""
        try:
            from dhee.core.kernel import get_last_session

            last_session = get_last_session(
                agent_id="claude-code",
                repo=repo,
                fallback_log_recovery=True,
                user_id=_ui_user_id(),
                requester_agent_id="dhee-ui",
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        try:
            claude_sessions = _find_claude_sessions(repo, limit=5)
        except Exception:  # noqa: BLE001
            claude_sessions = []
        return {
            "live": bool(last_session or claude_sessions),
            "repo": repo,
            "repo_config": pointer,
            "last_session": last_session,
            "claude_sessions": claude_sessions,
            "error": error or None,
        }

    # ── Local-context state file: workspaces + folders ───────────────
    # v1 schema (legacy): {"folders": {path: {...}}}
    # v2 schema (now):    {"schema_version": 2,
    #                      "workspaces": {ws_id: {"name": ..., "created_at": ...}},
    #                      "folders": {path: {workspace_id: ws_id, ...}}}
    # Reads accept both shapes. Writes always emit v2. Multi-workspace
    # is supported in storage today; the create endpoint enforces a
    # single-workspace cap so the UI stays simple — lift the cap by
    # changing one constant when we're ready.

    LOCAL_CONTEXT_SCHEMA_VERSION = 2
    DEFAULT_WORKSPACE_ID = "default"
    MAX_WORKSPACES = 1  # raise this when we ship multi-workspace UX

    def _local_context_state_path() -> Path:
        return Path.home() / ".dhee" / "local_context_folders.json"

    def _empty_state() -> Dict[str, Any]:
        return {
            "schema_version": LOCAL_CONTEXT_SCHEMA_VERSION,
            "workspaces": {},
            "folders": {},
            "projects": {},
            "teams": {},
        }

    def _read_local_context_state() -> Dict[str, Any]:
        path = _local_context_state_path()
        if not path.exists():
            return _empty_state()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return _empty_state()
        if not isinstance(data, dict):
            return _empty_state()

        folders_raw = data.get("folders")
        folders = folders_raw if isinstance(folders_raw, dict) else {}

        workspaces_raw = data.get("workspaces")
        workspaces = workspaces_raw if isinstance(workspaces_raw, dict) else {}

        # v1 → v2 migration: any pre-existing folders implicitly join
        # the default workspace. We auto-create that workspace here so
        # the rest of the code can assume every folder has one.
        if folders and not workspaces:
            workspaces = {
                DEFAULT_WORKSPACE_ID: {
                    "id": DEFAULT_WORKSPACE_ID,
                    "name": "My Workspace",
                    "created_at": _now_iso(),
                }
            }

        # Stamp every folder with a workspace_id so reads never have to
        # guess. Doesn't persist until the next write — that's fine.
        for fpath, frow in list(folders.items()):
            if not isinstance(frow, dict):
                continue
            if not frow.get("workspace_id"):
                frow["workspace_id"] = (
                    next(iter(workspaces)) if workspaces else DEFAULT_WORKSPACE_ID
                )

        out = {
            "schema_version": LOCAL_CONTEXT_SCHEMA_VERSION,
            "workspaces": workspaces,
            "folders": folders,
        }
        for key in ("projects", "teams", "collaborators"):
            if isinstance(data.get(key), dict):
                out[key] = data[key]
        return out

    def _ensure_default_workspace(state: Dict[str, Any]) -> str:
        """Make sure the state has at least one workspace; return its id.

        Single-workspace mode means: if no workspace exists yet, the
        first folder add creates the default one. The cap is enforced
        on explicit ``/api/workspace/create`` calls only; auto-creating
        the implicit default never hits the cap.
        """
        workspaces = state.get("workspaces") or {}
        if not isinstance(workspaces, dict):
            workspaces = {}
            state["workspaces"] = workspaces
        if workspaces:
            return next(iter(workspaces))
        ws_id = DEFAULT_WORKSPACE_ID
        workspaces[ws_id] = {
            "id": ws_id,
            "name": "My Workspace",
            "created_at": _now_iso(),
        }
        return ws_id

    def _write_local_context_state(state: Dict[str, Any]) -> None:
        path = _local_context_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Always normalise on write so the disk format is exactly v2.
        out = {
            "schema_version": LOCAL_CONTEXT_SCHEMA_VERSION,
            "workspaces": state.get("workspaces") or {},
            "folders": state.get("folders") or {},
        }
        for key in ("projects", "teams", "collaborators"):
            if isinstance(state.get(key), dict):
                out[key] = state[key]
        path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")

    def _folder_node_id(path: str) -> str:
        digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:14]
        return f"folder:{digest}"

    def _session_node_id(session: Dict[str, Any]) -> str:
        return str(session.get("id") or session.get("nativeSessionId") or hashlib.sha1(
            json.dumps(session, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:14])

    def _folder_label(path: str) -> str:
        return os.path.basename(path.rstrip(os.sep)) or path

    def _path_is_within(path: str, root: str) -> bool:
        try:
            return os.path.commonpath([path, root]) == root
        except ValueError:
            return False

    git_root_cache: Dict[str, str] = {}

    def _git_root_for_path(path: str) -> str:
        resolved = _abs_user_path(path)
        if not resolved:
            return ""
        probe = resolved if os.path.isdir(resolved) else os.path.dirname(resolved)
        if not probe:
            return ""
        cached = git_root_cache.get(probe)
        if cached is not None:
            return cached
        try:
            result = subprocess.run(
                ["git", "-C", probe, "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
                timeout=0.8,
            )
        except Exception:  # noqa: BLE001
            git_root_cache[probe] = ""
            return ""
        if result.returncode != 0:
            git_root_cache[probe] = ""
            return ""
        git_root_cache[probe] = _abs_user_path((result.stdout or "").strip()) or ""
        return git_root_cache[probe]

    def _best_configured_root(path: str, configured_paths: List[str]) -> str:
        best = ""
        best_len = -1
        for folder in configured_paths:
            try:
                common = os.path.commonpath([path, folder])
            except ValueError:
                continue
            if common == folder and len(folder) > best_len:
                best = folder
                best_len = len(folder)
        return best

    def _root_folder_for_path(path: Optional[str], configured_paths: Optional[List[str]] = None) -> str:
        resolved = _abs_user_path(path)
        if not resolved:
            return ""
        git_root = _git_root_for_path(resolved)
        if git_root:
            return git_root
        ui_repo = _abs_user_path(_ui_repo())
        if ui_repo and _path_is_within(resolved, ui_repo):
            return ui_repo
        configured_root = _best_configured_root(resolved, configured_paths or [])
        return configured_root or resolved

    def _folder_context_manager(path: str) -> Dict[str, Any]:
        label = _folder_label(path)
        return {
            "manager_id": f"context-manager:{_folder_node_id(path).split(':', 1)[-1]}",
            "display_name": f"{label} Context Manager",
            "folder_path": path,
            "charter": "Own context quality, freshness, and sharing for this local folder.",
            "status": "active",
        }

    ACTIVE_GRACE_SECONDS = 30 * 60  # survives quiet turns without reviving old rows

    def _is_session_active(session: Dict[str, Any]) -> bool:
        """Return whether a session should be shown in live-only surfaces.

        Older mirrored rows were sometimes left with ``state=active`` forever.
        Treat the stored state as a hint, then require either a fresh update,
        a current mirror marker, or a live native process/pid. This keeps
        FOLDERS and ROUTER from showing stale historical sessions as live.
        """
        state_value = str(session.get("state") or "").lower()
        if state_value in ("paused", "stale", "ended", "completed", "killed"):
            return False

        metadata = dict(session.get("metadata") or {})
        runtime = str(session.get("runtime_id") or session.get("runtime") or "").lower()
        updated_at = session.get("updated_at") or session.get("updatedAt")

        if _recent_enough(updated_at, seconds=ACTIVE_GRACE_SECONDS):
            return True

        # Claude's local registry gives us the real process id. Prefer that
        # over cwd-level process checks, because one Claude process in a repo
        # should not resurrect every old session that once used that folder.
        if runtime in {"claude-code", "claude"} and _pid_alive(metadata.get("pid")):
            return True

        return False

    def _local_context_graph_payload(*, active_only: bool = False) -> Dict[str, Any]:
        db = _get_db()
        state = _read_local_context_state()
        configured = state.get("folders") or {}
        workspaces = state.get("workspaces") or {}
        configured_paths = [
            _abs_user_path(path)
            for path in configured.keys()
            if _abs_user_path(path)
        ]
        process_paths = [
            _abs_user_path(proc.get("cwd"))
            for proc in _runtime_processes()
            if str(proc.get("runtime_id") or "") in {"codex", "claude-code"}
        ]
        extra_paths = [path for path in [*configured_paths, *process_paths] if path]
        sync = _mirror_runtime_sessions(db, extra_paths=extra_paths)
        raw_sessions = [
            session
            for session in list(sync.get("sessions") or [])
            if str(session.get("state") or "") != "stale"
        ]
        if not raw_sessions:
            raw_sessions = [
                session
                for session in db.list_agent_sessions(user_id=_ui_user_id(), limit=40)
                if str(session.get("state") or "") == "active"
                or _recent_enough(session.get("updated_at"), seconds=86_400)
            ]
        if active_only:
            raw_sessions = [s for s in raw_sessions if _is_session_active(s)]
        sessions = [_session_summary(db, session) for session in raw_sessions]
        folder_paths: List[str] = []
        seen_paths: set[str] = set()

        def add_folder(path: Optional[str]) -> None:
            resolved = _abs_user_path(path)
            if not resolved or resolved in seen_paths:
                return
            seen_paths.add(resolved)
            folder_paths.append(resolved)

        # Only the UI repo and configured paths seed the folder set. Sessions
        # whose cwd lives in a subfolder are clustered into their root folder
        # (git root → ui repo → nearest configured root), so the canvas shows
        # one node per project and all its sessions share a single context.
        add_folder(_root_folder_for_path(_ui_repo(), configured_paths) or _ui_repo())
        for path in configured_paths:
            add_folder(_root_folder_for_path(path, configured_paths) or path)

        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        folders: Dict[str, Dict[str, Any]] = {}
        session_counts: Dict[str, int] = Counter()
        active_counts: Dict[str, int] = Counter()
        latest_by_folder: Dict[str, str] = {}
        folder_for_session: Dict[str, str] = {}

        for session in sessions:
            cwd = _abs_user_path(session.get("cwd"))
            if not cwd:
                continue
            folder = _root_folder_for_path(cwd, configured_paths)
            if not folder:
                folder = cwd
            add_folder(folder)
            folder_for_session[_session_node_id(session)] = folder
            session_counts[folder] += 1
            if str(session.get("state") or "") == "active" or session.get("isCurrent"):
                active_counts[folder] += 1
            updated = str(session.get("updatedAt") or "")
            if updated and updated > latest_by_folder.get(folder, ""):
                latest_by_folder[folder] = updated

        def saved_rows_for_root(root: str) -> List[Dict[str, Any]]:
            rows: List[Dict[str, Any]] = []
            for saved_path, row in configured.items():
                resolved = _abs_user_path(saved_path)
                if not resolved or not isinstance(row, dict):
                    continue
                row_root = _root_folder_for_path(resolved, configured_paths) or resolved
                if row_root == root:
                    rows.append(row)
            return rows

        for path in folder_paths:
            saved_rows = saved_rows_for_root(path)
            if active_only and session_counts.get(path, 0) == 0 and not saved_rows:
                continue
            saved = saved_rows[0] if saved_rows else {}
            shared = any(bool(row.get("shared")) for row in saved_rows)
            linked = any(bool(row.get("linked")) for row in saved_rows)
            folders[path] = {
                "id": _folder_node_id(path),
                "type": "folder",
                "label": _folder_label(path),
                "health": "healthy" if (shared or linked) else "watch",
                "meta": {
                    "path": path,
                    "shared": shared,
                    "linked": linked,
                    "linked_at": saved.get("linked_at") if isinstance(saved, dict) else None,
                    "session_count": session_counts.get(path, 0),
                    "active_session_count": active_counts.get(path, 0),
                    "updated_at": latest_by_folder.get(path),
                    "source": "manual" if saved_rows else "session",
                    "context_manager": _folder_context_manager(path),
                },
            }

        for path in sorted(folders):
            nodes.append(folders[path])

        for session in sessions:
            session_id = _session_node_id(session)
            folder = folder_for_session.get(session_id)
            if not folder or folder not in folders:
                continue
            nodes.append({
                "id": session_id,
                "type": "session",
                "label": _session_title(
                    session.get("title"),
                    preview=session.get("preview"),
                    runtime=session.get("runtime"),
                    cwd=session.get("cwd"),
                    session_id=session.get("nativeSessionId") or session_id,
                ),
                "health": "healthy" if session.get("isCurrent") or session.get("state") == "active" else "watch",
                "meta": {
                    "folder_path": folder,
                    "cwd": session.get("cwd"),
                    "runtime": session.get("runtime"),
                    "state": session.get("state"),
                    "model": session.get("model"),
                    "updated_at": session.get("updatedAt"),
                    "task_id": session.get("taskId"),
                    "preview": session.get("preview"),
                    "permission_mode": session.get("permissionMode"),
                },
            })
            edges.append({"source": folders[folder]["id"], "target": session_id, "kind": "contains"})

        shared_folder_ids = [row["id"] for row in folders.values() if (row.get("meta") or {}).get("shared")]
        for idx in range(1, len(shared_folder_ids)):
            edges.append({
                "source": shared_folder_ids[idx - 1],
                "target": shared_folder_ids[idx],
                "kind": "shares",
            })

        # Workspace summary — list every known workspace so the UI can
        # render the workspace switcher (single-cap today, multi later).
        workspace_list = []
        for ws_id, ws_row in (workspaces or {}).items():
            if not isinstance(ws_row, dict):
                continue
            ws_folders = [
                p for p, f in (configured or {}).items()
                if isinstance(f, dict) and (f.get("workspace_id") or DEFAULT_WORKSPACE_ID) == ws_id
            ]
            workspace_list.append({
                "id": ws_id,
                "name": ws_row.get("name") or "Workspace",
                "created_at": ws_row.get("created_at"),
                "folder_count": len(ws_folders),
            })

        return {
            "live": True,
            "org_id": "local",
            "active_only": bool(active_only),
            "workspaces": workspace_list,
            "active_workspace_id": (workspace_list[0]["id"] if workspace_list else None),
            "nodes": nodes,
            "edges": edges,
            "totals": {
                "projects": 0,
                "teams": len(sessions),
                "repos": len(folders),
                "context_items": 0,
                "pending_proposals": 0,
                "folders": len(folders),
                "sessions": len(sessions),
                "shared_folders": len(shared_folder_ids),
            },
            "raw": {
                "mode": "local_context",
                "folders": list(folders.values()),
                "sessions": sessions,
                "shared_folder_paths": [
                    str((row.get("meta") or {}).get("path") or "")
                    for row in folders.values()
                    if (row.get("meta") or {}).get("shared")
                ],
                "context_index": [],
                "pending_proposals": [],
                "context_managers_by_team": {},
            },
        }

    @app.get("/api/local-context")
    def api_local_context() -> Dict[str, Any]:
        return _local_context_graph_payload()

    @app.post("/api/local-context/folders")
    def api_local_context_folder_add(payload: LocalContextFolderPayload) -> Dict[str, Any]:
        """Add a folder for shared context.

        Two-step flow that matches the CLI:

        1. If the path is inside a git repo, call ``repo_link.link()``.
           That creates ``<repo>/.dhee/``, installs the post-merge /
           post-checkout / post-rewrite hooks, and **mirrors the repo
           into the same JSON state file we read here** — so the user
           gets the full git-shared-context pipeline, not just a row in
           ``local_context_folders.json``.
        2. If the path isn't a git repo, fall through to the legacy
           behaviour: store the row, let the canvas cluster by path. We
           return ``link.linked: false`` so the UI can offer a "git init
           + link" follow-up.
        """
        path = _abs_user_path(payload.path)
        if not path:
            raise HTTPException(status_code=400, detail="path is required")

        link_info: Dict[str, Any] = {"linked": False}
        try:
            from dhee import repo_link

            try:
                info = repo_link.link(path)
                link_info = {"linked": True, **info}
                # repo_link.link() resolves to the git root, which may
                # differ from the user-picked sub-path. Anchor the state
                # row at the git root so clustering and link-state agree.
                path = info["repo_root"]
            except ValueError as exc:
                # Not a git repo. Keep the row but mark unlinked.
                link_info = {"linked": False, "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001
            link_info = {"linked": False, "reason": f"link error: {exc}"}

        state = _read_local_context_state()
        workspace_id = _ensure_default_workspace(state)
        folders = dict(state.get("folders") or {})
        current = dict(folders.get(path) or {})
        current.update({
            "path": path,
            "shared": bool(payload.shared),
            "workspace_id": current.get("workspace_id") or workspace_id,
            "updated_at": _now_iso(),
        })
        if link_info.get("linked"):
            current["linked"] = True
            current.setdefault("linked_at", current.get("linked_at") or _now_iso())
            current["source"] = "ui_link"
        folders[path] = current
        state["folders"] = folders
        _write_local_context_state(state)
        return {"ok": True, "folder": current, "link": link_info}

    @app.post("/api/local-context/folders/share")
    def api_local_context_folder_share(payload: LocalContextFolderPayload) -> Dict[str, Any]:
        return api_local_context_folder_add(payload)

    @app.post("/api/local-context/folders/link")
    def api_local_context_folder_link(payload: LocalContextFolderPayload) -> Dict[str, Any]:
        """Link an already-added folder to git-shared context.

        Same effect as ``api_local_context_folder_add`` for git repos,
        but exposed as its own endpoint so UI buttons read cleanly
        (``Link`` vs ``Add``). Returns 400 with a friendly reason if
        the path isn't inside a git repo.
        """
        path = _abs_user_path(payload.path)
        if not path:
            raise HTTPException(status_code=400, detail="path is required")
        try:
            from dhee import repo_link

            info = repo_link.link(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"link error: {exc}")

        state = _read_local_context_state()
        workspace_id = _ensure_default_workspace(state)
        folders = dict(state.get("folders") or {})
        repo_root = info["repo_root"]
        current = dict(folders.get(repo_root) or {})
        current.update({
            "path": repo_root,
            "shared": True,
            "linked": True,
            "linked_at": current.get("linked_at") or _now_iso(),
            "source": current.get("source") or "ui_link",
            "workspace_id": current.get("workspace_id") or workspace_id,
            "updated_at": _now_iso(),
        })
        folders[repo_root] = current
        state["folders"] = folders
        _write_local_context_state(state)
        return {"ok": True, "folder": current, "link": {"linked": True, **info}}

    @app.post("/api/local-context/folders/unlink")
    def api_local_context_folder_unlink(payload: LocalContextFolderPayload) -> Dict[str, Any]:
        path = _abs_user_path(payload.path)
        if not path:
            raise HTTPException(status_code=400, detail="path is required")
        try:
            from dhee import repo_link

            info = repo_link.unlink(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"unlink error: {exc}")
        return {"ok": True, "unlink": info}

    @app.get("/api/org/graph")
    def api_org_graph(
        org: Optional[str] = None,
        active: bool = False,
    ) -> Dict[str, Any]:
        del org
        try:
            return _local_context_graph_payload(active_only=active)
        except Exception as exc:  # noqa: BLE001
            return {"live": False, "nodes": [], "edges": [], "error": str(exc)}

    # ─── Workspace primitive (NEW: workspace = container of linked folders) ──
    # Distinct from the legacy ``/api/workspaces`` (workspace = project).
    # We namespace under ``/api/local/workspaces`` so the legacy routes
    # keep working until the rebrand finishes. Single workspace today
    # (MAX_WORKSPACES=1) — lift by raising the constant.

    @app.get("/api/local/workspaces")
    def api_workspaces_list() -> Dict[str, Any]:
        state = _read_local_context_state()
        ws = state.get("workspaces") or {}
        out = []
        for ws_id, row in ws.items():
            if not isinstance(row, dict):
                continue
            folders = [
                p for p, f in (state.get("folders") or {}).items()
                if isinstance(f, dict)
                and (f.get("workspace_id") or DEFAULT_WORKSPACE_ID) == ws_id
            ]
            out.append({
                "id": ws_id,
                "name": row.get("name") or "Workspace",
                "created_at": row.get("created_at"),
                "folder_count": len(folders),
                "folders": folders,
            })
        return {"workspaces": out, "max_workspaces": MAX_WORKSPACES}

    @app.post("/api/local/workspaces")
    def api_workspaces_create(payload: LocalWorkspaceCreatePayload) -> Dict[str, Any]:
        state = _read_local_context_state()
        existing = state.get("workspaces") or {}
        # Single-workspace cap. Storage allows more; the UX gate lives here.
        if len([w for w in existing.values() if isinstance(w, dict)]) >= MAX_WORKSPACES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"workspace cap reached ({MAX_WORKSPACES}); "
                    "multi-workspace is on the roadmap"
                ),
            )
        ws_id = (payload.id or DEFAULT_WORKSPACE_ID).strip() or DEFAULT_WORKSPACE_ID
        if ws_id in existing:
            raise HTTPException(status_code=409, detail=f"workspace {ws_id!r} exists")
        existing[ws_id] = {
            "id": ws_id,
            "name": (payload.name or "My Workspace").strip() or "My Workspace",
            "created_at": _now_iso(),
        }
        state["workspaces"] = existing
        _write_local_context_state(state)
        return {"ok": True, "workspace": existing[ws_id]}

    # ─── Per-session router view (Router dashboard data) ────────────
    # Returns one row per session, joining the agent_sessions table
    # with the router's ptr-store byte counts. This is what powers the
    # paginated table on the new Router landing page.

    @app.get("/api/router/sessions")
    def api_router_sessions(
        active: bool = True,
        cursor: Optional[str] = None,
        limit: int = 25,
        agent: Optional[str] = None,
        include_live_usage: bool = True,
    ) -> Dict[str, Any]:
        from dhee.router import ptr_store as _ptr
        from dhee.router import stats as _rstats

        # Per-session ptr-store roll-up. We walk the directory tree
        # ourselves rather than calling ``compute_stats`` because the
        # aggregate API doesn't expose the per-session breakdown we
        # need here.
        ptr_records: List[Dict[str, Any]] = []
        try:
            ptr_root = _ptr._root()
        except Exception:  # noqa: BLE001
            ptr_root = None
        if ptr_root is not None and ptr_root.exists():
            try:
                ptr_scan_limit = max(
                    100,
                    min(int(os.environ.get("DHEE_UI_ROUTER_PTR_SCAN_LIMIT", "1200")), 10000),
                )
            except (TypeError, ValueError):
                ptr_scan_limit = 1200

            def _path_mtime(path: Path) -> float:
                try:
                    return path.stat().st_mtime
                except OSError:
                    return 0.0

            scanned = 0
            session_dirs = sorted(
                (path for path in ptr_root.iterdir() if path.is_dir()),
                key=_path_mtime,
                reverse=True,
            )[: min(ptr_scan_limit, 500)]
            for sdir in session_dirs:
                for meta_file in sorted(sdir.glob("*.json"), key=_path_mtime, reverse=True):
                    if scanned >= ptr_scan_limit:
                        break
                    scanned += 1
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if not isinstance(meta, dict):
                        continue
                    tool_name = str(meta.get("tool") or "unknown")
                    ptr_records.append(
                        {
                            "bucket": sdir.name,
                            "chars": _rstats._stored_chars(meta, meta_file),
                            "tool": tool_name,
                            "agent": _rstats._agent_from_meta(meta),
                            "cwd": _abs_user_path(meta.get("cwd")),
                            "repo": _abs_user_path(meta.get("repo")),
                            "session_id": str(meta.get("session_id") or ""),
                            "thread_id": str(meta.get("thread_id") or ""),
                            "stored_at": _coerce_datetime(meta.get("stored_at"))
                            or _coerce_datetime(meta_file.stat().st_mtime),
                        }
                    )
                if scanned >= ptr_scan_limit:
                    break

        chars_per_token = float(getattr(_rstats, "CHARS_PER_TOKEN", 4))

        # Pull session records from the durable agent_sessions table.
        # We over-fetch then filter+sort+page in Python because the
        # join key (router session_id ↔ session row id) isn't indexed.
        db = _get_db()
        rows = db.list_agent_sessions(user_id=_ui_user_id(), limit=500) or []
        summaries = [_session_summary(db, row) for row in rows]
        codex_live_by_id: Dict[str, Dict[str, Any]] = {}
        if include_live_usage:
            try:
                for thread in _repo_codex_threads(_ui_repo(), limit=2):
                    usage = _router_codex_live_usage_from_thread(thread)
                    if not usage.get("available"):
                        continue
                    thread_id = str(thread.get("id") or "")
                    if not thread_id:
                        continue
                    codex_live_by_id[thread_id] = usage
                    codex_live_by_id[f"session:codex:{thread_id}"] = usage
            except Exception:  # noqa: BLE001
                codex_live_by_id = {}

        def router_bucket_for_cwd(cwd: Any) -> str:
            path = _abs_user_path(cwd)
            if not path:
                return ""
            user = os.environ.get("USER") or os.environ.get("LOGNAME") or "user"
            digest = hashlib.sha1(f"{user}|{path}".encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"s-{digest}"

        candidates: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for row, summary in zip(rows, summaries):
            session_id = str(summary.get("id") or "")
            native_id = str(summary.get("nativeSessionId") or "")
            cwd = _abs_user_path(summary.get("cwd"))
            updated_at = _coerce_datetime(summary.get("updatedAt"))
            started_at = _coerce_datetime(summary.get("startedAt"))
            row_active = _is_session_active(row)
            candidates.append(
                {
                    "session_id": session_id,
                    "native_id": native_id,
                    "runtime": _rstats._normalize_agent_id(summary.get("runtime")),
                    "cwd": cwd,
                    "repo_root": _git_root_for_path(cwd or "") or cwd or "",
                    "bucket": router_bucket_for_cwd(cwd),
                    "started_at": started_at,
                    "updated_at": updated_at,
                    "active": row_active,
                }
            )

        def score_record_for_session(rec: Dict[str, Any], cand: Dict[str, Any]) -> int:
            score = 0
            rec_session = str(rec.get("session_id") or "")
            rec_thread = str(rec.get("thread_id") or "")
            explicit_match = False
            if rec_session and rec_session in {cand["session_id"], cand["native_id"]}:
                score += 1000
                explicit_match = True
            if rec_thread and rec_thread in {cand["session_id"], cand["native_id"]}:
                score += 900
                explicit_match = True
            if rec.get("bucket") and rec.get("bucket") == cand.get("session_id"):
                score += 800
                explicit_match = True
            if rec.get("bucket") and rec.get("bucket") == cand.get("bucket"):
                score += 80

            rec_agent = _rstats._normalize_agent_id(rec.get("agent"))
            runtime = str(cand.get("runtime") or "unknown")
            if rec_agent in {"", "unknown"}:
                score += 5
            elif rec_agent == runtime:
                score += 40
            else:
                score -= 60

            rec_path = rec.get("cwd") or rec.get("repo") or ""
            if rec_path:
                if cand.get("cwd") and _paths_overlap(rec_path, cand["cwd"]):
                    score += 45
                elif cand.get("repo_root") and _paths_overlap(rec_path, cand["repo_root"]):
                    score += 35
                else:
                    score -= 30

            rec_time = rec.get("stored_at")
            if isinstance(rec_time, datetime):
                start = cand.get("started_at")
                end = now if cand.get("active") else cand.get("updated_at")
                if not explicit_match and isinstance(start, datetime):
                    start_floor = start - timedelta(minutes=5)
                    if rec_time < start_floor:
                        return -10_000
                if (
                    not explicit_match
                    and not isinstance(start, datetime)
                    and isinstance(end, datetime)
                    and rec_time < end - timedelta(seconds=ACTIVE_GRACE_SECONDS)
                ):
                    return -10_000
                if (
                    not explicit_match
                    and not cand.get("active")
                    and isinstance(end, datetime)
                    and rec_time > end + timedelta(minutes=5)
                ):
                    return -10_000
                if isinstance(start, datetime) and rec_time < start:
                    delta_hours = abs((start - rec_time).total_seconds()) / 3600
                    score -= min(40, int(delta_hours))
                elif isinstance(start, datetime):
                    score += 18
                if isinstance(end, datetime):
                    if rec_time <= end:
                        score += 28
                    else:
                        delta_hours = abs((rec_time - end).total_seconds()) / 3600
                        score -= min(50, int(delta_hours))

            return score

        per_session: Dict[str, Dict[str, Any]] = {
            str(summary.get("id") or ""): {"chars": 0, "calls": 0, "tools": {}, "agents": set()}
            for summary in summaries
            if summary.get("id")
        }
        for rec in ptr_records:
            best: Optional[Dict[str, Any]] = None
            best_score = -10_000
            for cand in candidates:
                score = score_record_for_session(rec, cand)
                if score > best_score:
                    best = cand
                    best_score = score
            if not best or best_score < 20:
                continue
            row = per_session.setdefault(
                str(best["session_id"]),
                {"chars": 0, "calls": 0, "tools": {}, "agents": set()},
            )
            row["calls"] += 1
            row["chars"] += int(rec.get("chars") or 0)
            tool_name = str(rec.get("tool") or "unknown")
            row["tools"][tool_name] = row["tools"].get(tool_name, 0) + 1
            row["agents"].add(str(rec.get("agent") or "unknown"))

        items: List[Dict[str, Any]] = []
        for row, summary in zip(rows, summaries):
            session_id = summary.get("id") or ""
            ptr_row = per_session.get(session_id) or {
                "chars": 0,
                "calls": 0,
                "tools": {},
                "agents": set(),
            }
            tokens_saved = int(ptr_row["chars"] / chars_per_token) if chars_per_token else 0
            known_agent_ids = sorted(
                a for a in ptr_row["agents"]
                if a and str(a).lower() not in {"unknown", "none", "null"}
            )
            agent_ids = known_agent_ids or [str(summary.get("runtime") or "unknown")]
            updated_at = summary.get("updatedAt") or ""
            pricing = _router_payg_pricing(summary.get("runtime"), summary.get("model"))
            estimated_cost_saved = (
                tokens_saved
                * float(pricing.get("input_cost_per_million") or 0.0)
                / 1_000_000
            )
            normalized_runtime = _rstats._normalize_agent_id(summary.get("runtime"))
            live_usage = None
            if include_live_usage and (
                normalized_runtime == "codex" or any(
                    _rstats._normalize_agent_id(a) == "codex" for a in agent_ids
                )
            ):
                live_usage = (
                    codex_live_by_id.get(str(session_id))
                    or codex_live_by_id.get(str(summary.get("nativeSessionId") or ""))
                )
            elif include_live_usage and (
                normalized_runtime == "claude-code" or any(
                    _rstats._normalize_agent_id(a) == "claude-code" for a in agent_ids
                )
            ):
                live_usage = _router_claude_live_usage_from_summary(summary)
            item = {
                "session_id": session_id,
                "title": _session_title(
                    summary.get("title"),
                    preview=summary.get("preview"),
                    runtime=summary.get("runtime"),
                    cwd=summary.get("cwd"),
                    session_id=summary.get("nativeSessionId") or session_id,
                ),
                "state": summary.get("state") or "",
                "agent": agent_ids[0] if agent_ids else "unknown",
                "agents": agent_ids,
                "cwd": summary.get("cwd") or "",
                "repo_root": _git_root_for_path(summary.get("cwd") or "") or summary.get("cwd") or "",
                "runtime": summary.get("runtime"),
                "model": summary.get("model"),
                "updated_at": updated_at,
                "started_at": summary.get("startedAt"),
                "tokens_saved": tokens_saved,
                "estimated_cost_saved_usd": round(estimated_cost_saved, 6),
                "pricing": pricing,
                "live_usage": live_usage,
                "router_calls": int(ptr_row["calls"]),
                "tool_breakdown": dict(ptr_row["tools"]),
                "active": _is_session_active(row),
                "task": {
                    "id": summary.get("taskId"),
                    "status": summary.get("taskStatus"),
                },
                "preview": summary.get("preview") or "",
            }
            if active and not item["active"]:
                continue
            if agent and item["agent"] != _rstats._normalize_agent_id(agent):
                continue
            items.append(item)

        # Sort newest-first; cursor is just an opaque (updated_at, session_id)
        # tuple so a future paginator can resume past it.
        items.sort(key=lambda r: (r["updated_at"], r["session_id"]), reverse=True)

        if cursor:
            # Cursor is "<updated_at>|<session_id>". Drop everything <= cursor.
            try:
                cur_updated, cur_id = cursor.split("|", 1)
            except ValueError:
                cur_updated, cur_id = "", ""
            items = [
                r for r in items
                if (r["updated_at"], r["session_id"]) < (cur_updated, cur_id)
            ]

        bounded_limit = max(1, min(int(limit), 100))
        page = items[:bounded_limit]
        next_cursor = None
        if len(items) > bounded_limit:
            tail = page[-1]
            next_cursor = f"{tail['updated_at']}|{tail['session_id']}"

        estimated_total = round(
            sum(float(r.get("estimated_cost_saved_usd") or 0.0) for r in items),
            6,
        )
        return {
            "items": page,
            "next_cursor": next_cursor,
            "active_only": bool(active),
            "totals": {
                "tokens_saved": sum(r["tokens_saved"] for r in items),
                "estimated_cost_saved_usd": estimated_total,
                "theoretical_api_value_usd": estimated_total,
                "realized_cost_saved_usd": _budget_cap_usd(estimated_total, period="month"),
                "router_calls": sum(r["router_calls"] for r in items),
                "sessions": len(items),
            },
            "budget": _router_budget_payload(),
            "money_math": {
                "tokens_basis": "raw input tokens avoided by cached router context",
                "dollar_basis": "avoided input tokens multiplied by mapped official provider input rates",
                "realized_cap": "monthly paid AI budget",
                "honesty_note": (
                    "Theoretical API value can be higher than the user's paid budget. "
                    "Dhee claims realized savings only up to that configured budget."
                ),
            },
        }

    # ─── Context Management screen data ─────────────────────────────
    # One endpoint per folder: returns repo entries (shared via git),
    # personal memories that have been promoted into this repo, and
    # personal memories that came from this repo via demote. Plus a
    # share matrix listing every other linked repo and how many
    # entries cross between them. The UI uses this to render the
    # per-folder context management screen + bidirectional shared
    # context view.

    @app.get("/api/context/entries")
    def api_context_entries(
        repo: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        from dhee import repo_link

        target_path = _abs_user_path(repo) if repo else None
        repo_root = repo_link.repo_for_path(target_path) if target_path else None
        if repo_root is None:
            # Fallback: pick the first linked repo so the UI has something.
            links = repo_link.list_links() or {}
            if not links:
                return {
                    "repo_root": None,
                    "linked": False,
                    "repo_entries": [],
                    "promoted_in": [],
                    "demoted_out": [],
                    "share_matrix": [],
                }
            repo_root = Path(next(iter(links.keys())))

        entries = repo_link.list_entries(repo_root)
        limit = max(1, min(int(limit), 1000))
        entry_rows = [e.to_json() for e in entries[:limit]]

        # Personal memories cross-referenced with this repo. We scan
        # the personal store for ``promoted_to`` / ``demoted_from_repo``
        # markers we wrote during the original promote/demote calls.
        promoted_in: List[Dict[str, Any]] = []
        demoted_out: List[Dict[str, Any]] = []
        try:
            from dhee.cli_config import get_memory_instance

            memory = get_memory_instance(None)
            try:
                personal = memory.get_all(
                    user_id=_ui_user_id(),
                    limit=500,
                ) or {}
            except Exception:  # noqa: BLE001
                personal = {}
            for record in personal.get("results") or []:
                if not isinstance(record, dict):
                    continue
                meta = record.get("metadata") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except (ValueError, TypeError):
                        meta = {}
                # Promoted INTO this repo (this personal memory landed
                # in <repo>/.dhee/ as an entry).
                for promoted in (meta.get("promoted_to") or []):
                    if not isinstance(promoted, dict):
                        continue
                    if str(promoted.get("repo_root") or "") == str(repo_root):
                        promoted_in.append({
                            "memory_id": record.get("id"),
                            "memory": record.get("memory") or "",
                            "entry_id": promoted.get("entry_id"),
                            "promoted_at": promoted.get("at"),
                        })
                # Demoted OUT of this repo (a repo entry was copied
                # into the user's personal store).
                if str(meta.get("demoted_from_repo") or "") == str(repo_root):
                    demoted_out.append({
                        "memory_id": record.get("id"),
                        "memory": record.get("memory") or "",
                        "entry_id": meta.get("demoted_from_entry"),
                        "demoted_at": meta.get("demoted_at"),
                    })
        except Exception as exc:  # noqa: BLE001
            log.debug("context/entries: personal cross-ref skipped (%s)", exc)

        # Share matrix: which other linked repos exist, how many
        # entries each side has (lightweight overview, no fusion of
        # the entries themselves).
        share_matrix: List[Dict[str, Any]] = []
        try:
            for other_root_str in (repo_link.list_links() or {}).keys():
                other_root = Path(other_root_str)
                if other_root == repo_root:
                    continue
                other_entries = repo_link.list_entries(other_root)
                share_matrix.append({
                    "repo_root": str(other_root),
                    "label": other_root.name,
                    "entry_count": len(other_entries),
                })
        except Exception as exc:  # noqa: BLE001
            log.debug("context/entries: share_matrix skipped (%s)", exc)

        return {
            "repo_root": str(repo_root),
            "linked": True,
            "repo_entries": entry_rows,
            "promoted_in": promoted_in,
            "demoted_out": demoted_out,
            "share_matrix": share_matrix,
            "totals": {
                "repo_entries": len(entries),
                "promoted_in": len(promoted_in),
                "demoted_out": len(demoted_out),
                "linked_peers": len(share_matrix),
            },
        }

    @app.post("/api/context/promote")
    def api_context_promote(payload: ContextPromotePayload) -> Dict[str, Any]:
        from dhee import repo_link
        from dhee.cli_config import get_memory_instance

        memory = get_memory_instance(None)
        try:
            entry, repo_root = repo_link.promote(
                payload.memory_id,
                memory=memory,
                repo=payload.repo,
                kind=payload.kind or "learning",
                title=payload.title,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "entry": entry.to_json(), "repo_root": str(repo_root)}

    @app.post("/api/context/demote")
    def api_context_demote(payload: ContextDemotePayload) -> Dict[str, Any]:
        from dhee import repo_link
        from dhee.cli_config import get_memory_instance

        memory = get_memory_instance(None)
        try:
            new_id, entry = repo_link.demote(
                payload.entry_id,
                memory=memory,
                repo=payload.repo,
                user_id=_ui_user_id(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "memory_id": new_id, "entry": entry.to_json()}

    @app.delete("/api/local/workspaces/{ws_id}")
    def api_workspaces_delete(ws_id: str) -> Dict[str, Any]:
        state = _read_local_context_state()
        existing = state.get("workspaces") or {}
        if ws_id not in existing:
            raise HTTPException(status_code=404, detail="workspace not found")
        # Folders that belonged to it become orphaned with workspace_id=None;
        # the next read will re-stamp them onto the surviving default.
        for fpath, frow in (state.get("folders") or {}).items():
            if isinstance(frow, dict) and frow.get("workspace_id") == ws_id:
                frow["workspace_id"] = None
        existing.pop(ws_id, None)
        state["workspaces"] = existing
        _write_local_context_state(state)
        return {"ok": True, "deleted": ws_id}

    @app.get("/api/context/items")
    def api_context_items(
        team: Optional[str] = None,
        scope: Optional[str] = None,
        kind: Optional[str] = None,
        project: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                rows = svc.store.list_context_items(
                    org_id=svc.org_id,
                    team_id=team,
                    project_id=project,
                    scope=scope,
                    limit=int(limit),
                )
                if kind:
                    rows = [r for r in rows if r.get("kind") == kind]
            finally:
                svc.close()
            return {"live": True, "items": rows}
        except Exception as exc:  # noqa: BLE001
            return {"live": False, "items": [], "error": str(exc)}

    @app.get("/api/context/usage")
    def api_context_usage(
        team: Optional[str] = None,
        scope: Optional[str] = None,
        kind: Optional[str] = None,
        project: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                rows = svc.store.list_context_items(
                    org_id=svc.org_id,
                    team_id=team,
                    project_id=project,
                    scope=scope,
                    limit=max(1, min(int(limit), 1000)),
                )
                if kind:
                    rows = [r for r in rows if r.get("kind") == kind]
            finally:
                svc.close()
        except Exception as exc:  # noqa: BLE001
            return {
                "live": False,
                "items": [],
                "totals": {
                    "contexts": 0,
                    "used_contexts": 0,
                    "usage_count": 0,
                    "tokens_served": 0,
                    "proven_tokens_saved": 0,
                    "theoretical_api_value_usd": 0.0,
                    "realized_cost_saved_usd": 0.0,
                },
                "budget": _router_budget_payload(),
                "error": str(exc),
            }

        context_ids = {str(row.get("context_id")) for row in rows if row.get("context_id")}
        proven = _context_proven_router_savings(context_ids)
        items: List[Dict[str, Any]] = []
        for row in rows:
            cid = str(row.get("context_id") or "")
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            usage_count = _metadata_int(row.get("usage_count"))
            token_cost = _metadata_int(
                row.get("token_cost"),
                max(1, len(str(row.get("summary") or row.get("content") or "")) // 4),
            )
            meta_saved = _metadata_int(
                meta.get("proven_tokens_saved"),
                meta.get("saved_tokens"),
                meta.get("tokens_saved"),
                meta.get("raw_tokens_saved"),
            )
            if not meta_saved:
                raw_tokens = _metadata_int(meta.get("raw_tokens"))
                summary_tokens = _metadata_int(meta.get("summary_tokens"), row.get("token_cost"))
                meta_saved = max(0, raw_tokens - summary_tokens) if raw_tokens else 0
            meta_value = 0.0
            try:
                meta_value = max(
                    0.0,
                    float(
                        meta.get("proven_cost_saved_usd")
                        or meta.get("cost_saved_usd")
                        or meta.get("api_value_usd")
                        or 0.0
                    ),
                )
            except (TypeError, ValueError):
                meta_value = 0.0
            router_row = proven.get(cid) or {}
            proven_tokens = int(router_row.get("tokens") or 0) + meta_saved
            api_value = float(router_row.get("api_value_usd") or 0.0) + meta_value
            items.append({
                "context_id": cid,
                "title": row.get("title") or cid,
                "scope": row.get("scope"),
                "kind": row.get("kind"),
                "team_id": row.get("team_id"),
                "project_id": row.get("project_id"),
                "usage_count": usage_count,
                "last_used_at": row.get("last_used_at"),
                "token_cost": token_cost,
                "tokens_served": usage_count * token_cost,
                "proven_tokens_saved": proven_tokens,
                "theoretical_api_value_usd": round(api_value, 6),
                "realized_cost_saved_usd": 0.0,
                "quality_score": row.get("quality_score"),
                "freshness_score": row.get("freshness_score"),
                "confidence": row.get("confidence"),
                "evidence": {
                    "router_calls": int(router_row.get("calls") or 0),
                    "metadata_tokens": meta_saved,
                    "has_direct_savings_evidence": bool(proven_tokens or api_value),
                },
            })
        total_api_value = sum(float(item["theoretical_api_value_usd"] or 0.0) for item in items)
        monthly_cap = float(_router_budget_payload().get("monthly_budget_usd") or 0.0)
        cap_factor = 1.0
        if total_api_value > 0 and monthly_cap > 0:
            cap_factor = min(1.0, monthly_cap / total_api_value)
        for item in items:
            item["realized_cost_saved_usd"] = round(
                float(item["theoretical_api_value_usd"] or 0.0) * cap_factor,
                6,
            )
        items.sort(
            key=lambda item: (
                -int(item.get("usage_count") or 0),
                -int(item.get("proven_tokens_saved") or 0),
                str(item.get("title") or ""),
            )
        )
        realized_total = round(sum(float(item["realized_cost_saved_usd"] or 0.0) for item in items), 6)
        return {
            "live": True,
            "items": items[: max(1, min(int(limit), 1000))],
            "totals": {
                "contexts": len(items),
                "used_contexts": sum(1 for item in items if int(item.get("usage_count") or 0) > 0),
                "usage_count": sum(int(item.get("usage_count") or 0) for item in items),
                "tokens_served": sum(int(item.get("tokens_served") or 0) for item in items),
                "proven_tokens_saved": sum(int(item.get("proven_tokens_saved") or 0) for item in items),
                "theoretical_api_value_usd": round(total_api_value, 6),
                "realized_cost_saved_usd": realized_total,
            },
            "budget": _router_budget_payload(),
            "money_math": {
                "usage_basis": "actual context injections recorded by Dhee",
                "savings_basis": (
                    "per-context savings require direct evidence: router metadata "
                    "with context ids or explicit saved-token metadata on the context item"
                ),
                "unattributed_note": (
                    "Router savings that cannot be tied to a specific context stay in "
                    "the Router total and are not allocated across contexts."
                ),
            },
        }

    @app.post("/api/context")
    def api_context_upsert(payload: ContextUpsertPayload) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                if payload.context_id:
                    existing = svc.store.get_context_item(svc.org_id, payload.context_id) or {}
                    expected_hash = str(payload.expected_content_hash or "").strip()
                    current_hash = str(existing.get("content_hash") or "").strip()
                    if expected_hash and current_hash and expected_hash != current_hash:
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "reason": "context_changed",
                                "context_id": payload.context_id,
                                "expected_content_hash": expected_hash,
                                "current_content_hash": current_hash,
                            },
                        )
                    merged = {
                        **existing,
                        "context_id": payload.context_id,
                        "title": payload.title,
                        "content": payload.content,
                        "scope": payload.scope,
                        "kind": payload.kind,
                        "project_id": payload.project_id or existing.get("project_id"),
                        "team_id": payload.team_id or existing.get("team_id"),
                        "user_id": payload.user_id or existing.get("user_id"),
                        "tags": payload.tags or existing.get("tags") or [],
                        "summary": payload.summary or "",
                        "metadata": payload.metadata or existing.get("metadata") or {},
                        "org_id": svc.org_id,
                        "status": "active",
                    }
                    item = svc.store.add_context_item(merged)
                else:
                    item = svc.add_context(
                        title=payload.title,
                        content=payload.content,
                        scope=payload.scope,
                        kind=payload.kind,
                        project_id=payload.project_id,
                        team_id=payload.team_id,
                        user_id=payload.user_id,
                        tags=payload.tags or [],
                        summary=payload.summary or "",
                        metadata=payload.metadata or {},
                    )
            finally:
                svc.close()
            return {"ok": True, "item": item}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/proposals")
    def api_proposal_create(payload: ProposalCreatePayload) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                item = svc.propose_context(
                    title=payload.title,
                    content=payload.content,
                    scope=payload.scope,
                    kind=payload.kind,
                    proposed_by_user_id=payload.proposed_by_user_id,
                    project_id=payload.project_id,
                    team_id=payload.team_id,
                    tags=payload.tags or [],
                    supersedes_id=payload.supersedes_id,
                    metadata=payload.metadata or {},
                )
            finally:
                svc.close()
            return {"ok": True, "proposal": item}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/proposals/{context_id}/approve")
    def api_proposal_approve(context_id: str, payload: ProposalDecisionPayload) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                item = svc.approve_proposal(context_id, reviewer_user_id=payload.reviewer_user_id)
            finally:
                svc.close()
            return {"ok": True, "proposal": item}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/proposals/{context_id}/reject")
    def api_proposal_reject(context_id: str, payload: ProposalDecisionPayload) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                item = svc.reject_proposal(context_id, reviewer_user_id=payload.reviewer_user_id)
            finally:
                svc.close()
            return {"ok": True, "proposal": item}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/inbox")
    def api_inbox(
        team: Optional[str] = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        proposals: List[Dict[str, Any]] = []
        findings: List[Dict[str, Any]] = []
        try:
            svc = _enterprise_service()
            try:
                team_ids = [team] if team else None
                box = svc.inbox_for_viewer(user_id=user, team_ids=team_ids)
                proposals = box.get("proposals") or []
                findings = box.get("findings") or []
            finally:
                svc.close()
        except Exception:  # noqa: BLE001
            pass
        # Memory conflicts (existing path)
        try:
            from dhee.full_memory import FullMemory  # type: ignore

            mem = FullMemory()
            mem_conflicts = mem.list_conflicts() or []
            mem.close()
        except Exception:  # noqa: BLE001
            mem_conflicts = []
        return {
            "live": True,
            "proposals": proposals,
            "findings": findings,
            "conflicts": mem_conflicts,
            "totals": {
                "proposals": len(proposals),
                "findings": len(findings),
                "conflicts": len(mem_conflicts),
            },
        }

    @app.post("/api/findings/{finding_id}/resolve")
    def api_finding_resolve(finding_id: str, payload: FindingResolvePayload) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                item = svc.resolve_finding(finding_id, resolved_by=payload.resolved_by)
            finally:
                svc.close()
            return {"ok": True, "finding": item}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/backlinks")
    def api_backlinks(context_id: str, limit: int = 50) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                links = svc.list_backlinks(context_id, limit=int(limit))
                shares = svc.store.list_context_shares(org_id=svc.org_id, context_id=context_id)
            finally:
                svc.close()
            return {"live": True, "backlinks": links, "shares": shares}
        except Exception as exc:  # noqa: BLE001
            return {"live": False, "backlinks": [], "shares": [], "error": str(exc)}

    # ─── Product screens: context firewall era ───────────────────────────────

    def _product_safe(label: str, fn, fallback: Any) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            log.debug("ui product payload %s failed: %s", label, exc)
            if isinstance(fallback, dict):
                return {**fallback, "live": False, "error": str(exc)}
            return fallback

    def _learning_preview(value: Any, limit: int = 420) -> str:
        text = str(value or "")
        text = re.sub(r"<think>[\s\S]*?</think>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"</?think>", " ", text, flags=re.IGNORECASE)
        lines: List[str] = []
        for line in text.splitlines():
            clean = " ".join(line.strip().split())
            if not clean:
                continue
            lowered = clean.lower()
            if lowered.startswith(("model:", "session:", "source:", "messages:", "representative turns:")):
                continue
            lines.append(clean)
        preview = " ".join(lines)
        preview = " ".join(preview.split())
        if len(preview) <= limit:
            return preview
        clipped = preview[: max(0, limit - 3)].rstrip()
        boundary = max(clipped.rfind("."), clipped.rfind(";"), clipped.rfind(","), clipped.rfind(" "))
        if boundary > limit * 0.65:
            clipped = clipped[:boundary].rstrip()
        return f"{clipped}..."

    def _learning_needs_distillation(value: Any) -> bool:
        text = str(value or "").lower()
        return "representative turns:" in text or bool(re.search(r"(^|\n)\s*(user|assistant|tool):", text))

    def _compact_learning_row(row: Dict[str, Any]) -> Dict[str, Any]:
        evidence = row.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = []
        success_count = int(row.get("success_count") or 0)
        failure_count = int(row.get("failure_count") or 0)
        if row.get("status") == "promoted":
            gate = "approved"
        elif success_count:
            gate = "successful outcome"
        elif failure_count:
            gate = "failure evidence"
        elif evidence:
            gate = str((evidence[0] or {}).get("kind") or "evidence backed")
        else:
            gate = "needs approval"
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        body = str(row.get("body") or "")
        needs_distillation = _learning_needs_distillation(body)
        preview = _learning_preview(body)
        if needs_distillation:
            preview = "Raw session import compacted for review. Promote only after distilling a reusable, evidence-backed rule."
        return {
            "id": row.get("id"),
            "kind": row.get("kind"),
            "title": _learning_preview(row.get("title"), limit=140) or str(row.get("id") or "Learning"),
            "body": preview,
            "preview": preview,
            "source_agent_id": row.get("source_agent_id"),
            "source_harness": row.get("source_harness"),
            "source_model": metadata.get("source_model") or metadata.get("model"),
            "task_type": row.get("task_type"),
            "repo": row.get("repo"),
            "scope": row.get("scope"),
            "confidence": row.get("confidence"),
            "utility": row.get("utility"),
            "status": row.get("status"),
            "reuse_count": row.get("reuse_count"),
            "success_count": success_count,
            "failure_count": failure_count,
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "promoted_at": row.get("promoted_at"),
            "rejected_reason": row.get("rejected_reason"),
            "evidence_gate": gate,
            "evidence_count": len(evidence),
            "raw_body_chars": len(body),
            "needs_distillation": needs_distillation,
        }

    def _learning_snapshot(limit: int = 80) -> Dict[str, Any]:
        from dhee.core.learnings import LearningExchange

        exchange = LearningExchange()
        rows = [_compact_learning_row(item.to_dict()) for item in exchange.list()]
        rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        counts: Dict[str, int] = {}
        for row in rows:
            status_key = str(row.get("status") or "candidate")
            counts[status_key] = counts.get(status_key, 0) + 1
        return {
            "live": True,
            "repo": _ui_repo(),
            "items": rows[: max(1, min(int(limit), 500))],
            "totals": {
                "all": len(rows),
                "candidate": counts.get("candidate", 0),
                "promoted": counts.get("promoted", 0),
                "rejected": counts.get("rejected", 0),
                "archived": counts.get("archived", 0),
            },
        }

    def _ui_fast_continuity() -> Dict[str, Any]:
        repo = _ui_repo()
        pointer = _resolve_repo_pointer()
        last_session: Optional[Dict[str, Any]] = None
        error = ""
        for agent_id in ("codex", "claude-code", "mcp-server"):
            try:
                from dhee.core.kernel import get_last_session

                last_session = get_last_session(
                    agent_id=agent_id,
                    repo=repo,
                    fallback_log_recovery=False,
                    user_id=_ui_user_id(),
                    requester_agent_id="dhee-ui",
                )
                if last_session:
                    break
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        return {
            "live": bool(last_session),
            "repo": repo,
            "repo_config": pointer,
            "last_session": last_session,
            "claude_sessions": [],
            "error": error or None,
        }

    def _ui_light_router_sessions(active: bool = False, limit: int = 20) -> Dict[str, Any]:
        try:
            return api_router_sessions(
                active=active,
                limit=max(1, min(int(limit), 50)),
                include_live_usage=False,
            )
        except Exception as exc:  # noqa: BLE001
            return {"items": [], "totals": {"sessions": 0}, "live": False, "error": str(exc)}

    def _ui_pack_counts() -> Dict[str, Any]:
        db = _get_db()
        counts: Dict[str, Any] = {"memories": 0, "artifacts": 0, "repo_context_entries": 0}
        try:
            counts["memories"] = len(db.get_all_memories(user_id=_ui_user_id(), limit=200000) or [])
        except Exception:  # noqa: BLE001
            pass
        try:
            counts["artifacts"] = len(db.list_artifacts(user_id=_ui_user_id(), limit=200000) or [])
        except Exception:  # noqa: BLE001
            pass
        try:
            counts["repo_context_entries"] = int(
                (api_context_entries(repo=_ui_repo(), limit=1000).get("totals") or {}).get("repo_entries") or 0
            )
        except Exception:  # noqa: BLE001
            pass
        return counts

    def _ui_workspace_summary() -> Dict[str, Any]:
        """Small command-center summary; avoid shipping the full workspace tree."""
        db = _get_db()
        try:
            workspaces = db.list_workspaces(user_id=_ui_user_id(), limit=200) or []
        except Exception:  # noqa: BLE001
            workspaces = []
        project_count = 0
        current_project_id = ""
        for workspace in workspaces[:50]:
            workspace_id = str(workspace.get("id") or "")
            if not workspace_id:
                continue
            try:
                projects = db.list_workspace_projects(
                    workspace_id=workspace_id,
                    user_id=_ui_user_id(),
                    limit=200,
                ) or []
            except Exception:  # noqa: BLE001
                projects = []
            project_count += len(projects)
            if not current_project_id and projects:
                current_project_id = str(projects[0].get("id") or "")
        return {
            "count": len(workspaces),
            "project_count": project_count,
            "currentWorkspaceId": str(workspaces[0].get("id") or "") if workspaces else "",
            "currentProjectId": current_project_id,
        }

    def _latest_dheemem_packs(limit: int = 8) -> List[Dict[str, Any]]:
        try:
            from dhee.protocol import inspect_pack
        except Exception:  # noqa: BLE001
            inspect_pack = None  # type: ignore[assignment]
        roots = [
            Path(_dhee_data_dir_str()) / "exports",
            Path(_ui_repo()),
        ]
        seen: set[str] = set()
        packs: List[Dict[str, Any]] = []
        for root in roots:
            if not root.exists():
                continue
            for path in root.glob("*.dheemem"):
                resolved = str(path.expanduser().resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                stat = path.stat()
                row: Dict[str, Any] = {
                    "path": resolved,
                    "name": path.name,
                    "size_bytes": stat.st_size,
                    "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    "verified": False,
                }
                if inspect_pack is not None:
                    try:
                        inspected = inspect_pack(path)
                        row["verified"] = True
                        row["format"] = inspected.get("format")
                        row["version"] = inspected.get("version")
                        row["created_at"] = inspected.get("created_at")
                        row["files"] = inspected.get("files")
                        row["handoff"] = inspected.get("handoff")
                        row["repo_context"] = inspected.get("repo_context")
                    except Exception as exc:  # noqa: BLE001
                        row["error"] = str(exc)
                packs.append(row)
        packs.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return packs[: max(1, min(int(limit), 50))]

    @app.get("/api/ui/command-center")
    def api_ui_command_center() -> Dict[str, Any]:
        router_sessions = _product_safe("router_sessions", lambda: _ui_light_router_sessions(active=False, limit=12), {"items": [], "totals": {}})
        router_totals = router_sessions.get("totals") or {}
        router = {
            "live": bool(router_sessions.get("items")),
            "sessionTokensSaved": int(router_totals.get("tokens_saved") or 0),
            "totalCalls": int(router_totals.get("router_calls") or 0),
            "estimatedCostSavedUsd": router_totals.get("estimated_cost_saved_usd") or 0,
        }
        continuity = _product_safe("continuity", lambda: _ui_fast_continuity(), {"live": False})
        task_data = _product_safe("tasks", lambda: tasks(), {"tasks": []})
        inbox = _product_safe("inbox", lambda: api_inbox(user=_ui_user_id()), {"totals": {}})
        context_entries = _product_safe(
            "context_entries",
            lambda: api_context_entries(repo=_ui_repo(), limit=12),
            {"linked": False, "repo_entries": [], "totals": {}},
        )
        learnings = _product_safe("learnings", lambda: _learning_snapshot(limit=24), {"items": [], "totals": {}})
        workspaces = _product_safe("workspaces", lambda: _ui_workspace_summary(), {"count": 0, "project_count": 0})
        sessions = router_sessions.get("items") or []
        active_task = next(
            (row for row in (task_data.get("tasks") or []) if str(row.get("status") or "") == "active"),
            (task_data.get("tasks") or [None])[0],
        )
        next_action = "Start a routed agent task from this repo"
        if active_task:
            next_action = "Resume the active task with Dhee handoff"
        elif int((context_entries.get("totals") or {}).get("repo_entries") or 0) == 0:
            next_action = "Promote the first repo context entry"
        elif int((learnings.get("totals") or {}).get("candidate") or 0) > 0:
            next_action = "Review candidate learnings"
        return {
            "live": True,
            "repo": _ui_repo(),
            "active_task": active_task,
            "router": router,
            "router_sessions": sessions[:8],
            "continuity": continuity,
            "context": context_entries,
            "learnings": learnings,
            "inbox": inbox,
            "workspaces": workspaces,
            "next_action": next_action,
            "dhee_aliases": [
                "dhee://state/current",
                "dhee://handoff/latest",
                "dhee://router/ptr/<id>",
                "dhee://repo/context/<id>",
            ],
        }

    @app.get("/api/ui/handoff")
    def api_ui_handoff() -> Dict[str, Any]:
        continuity = _product_safe("continuity", lambda: _ui_fast_continuity(), {"live": False})
        task_data = _product_safe("tasks", lambda: tasks(), {"tasks": []})
        router_sessions = _product_safe("router_sessions", lambda: _ui_light_router_sessions(active=False, limit=20), {"items": []})
        last_session = continuity.get("last_session") or {}
        confidence = 0.0
        if last_session:
            confidence += 0.35
        if last_session.get("files_touched") or last_session.get("filesTouched"):
            confidence += 0.2
        if last_session.get("todos") or last_session.get("decisions"):
            confidence += 0.25
        if task_data.get("tasks"):
            confidence += 0.2
        return {
            "live": bool(continuity.get("live") or task_data.get("tasks")),
            "repo": _ui_repo(),
            "continuity": continuity,
            "tasks": task_data.get("tasks") or [],
            "sessions": router_sessions.get("items") or [],
            "resume_confidence": round(min(confidence, 1.0), 2),
            "command": f"dhee handoff --repo {_ui_repo()} --json",
        }

    @app.get("/api/ui/proof-replay")
    def api_ui_proof_replay(limit: int = 80) -> Dict[str, Any]:
        limit = max(10, min(int(limit), 200))
        events: List[Dict[str, Any]] = []
        router_sessions = _product_safe("router_sessions", lambda: _ui_light_router_sessions(active=False, limit=30), {"items": []})
        for row in router_sessions.get("items") or []:
            title = str(row.get("title") or row.get("session_id") or "Agent session")
            events.append({
                "id": f"router:{row.get('session_id')}",
                "time": row.get("updated_at") or row.get("started_at"),
                "kind": "digest",
                "title": f"Context firewall digested {int(row.get('router_calls') or 0)} tool call(s)",
                "detail": title,
                "agent": row.get("agent") or row.get("runtime"),
                "tokens_saved": row.get("tokens_saved") or 0,
                "source": "router session",
                "derived": False,
            })
            for tool, calls in (row.get("tool_breakdown") or {}).items():
                events.append({
                    "id": f"tool:{row.get('session_id')}:{tool}",
                    "time": row.get("updated_at") or row.get("started_at"),
                    "kind": "hidden_raw",
                    "title": f"{tool} raw output held behind pointer",
                    "detail": f"{calls} routed call(s) stayed outside prompt context until expansion.",
                    "source": "router metadata",
                    "derived": True,
                })
        try:
            db = _get_db()
            for task in db.list_shared_tasks(user_id=_ui_user_id(), repo=_ui_repo(), limit=12) or []:
                task_id = str(task.get("id") or "")
                for result in db.list_shared_task_results(shared_task_id=task_id, limit=12) or []:
                    events.append({
                        "id": f"result:{result.get('id')}",
                        "time": result.get("updated_at") or result.get("created_at"),
                        "kind": "evidence",
                        "title": str(result.get("tool_name") or "Shared result"),
                        "detail": str(result.get("digest") or result.get("ptr") or "Pointer-backed result"),
                        "ptr": result.get("ptr"),
                        "source": "shared task result",
                        "derived": False,
                    })
        except Exception:  # noqa: BLE001
            pass
        context_entries = _product_safe(
            "context_entries",
            lambda: api_context_entries(repo=_ui_repo(), limit=30),
            {"repo_entries": []},
        )
        for entry in context_entries.get("repo_entries") or []:
            events.append({
                "id": f"context:{entry.get('id') or entry.get('context_id')}",
                "time": entry.get("updated_at") or entry.get("created_at"),
                "kind": "injected_context",
                "title": str(entry.get("title") or "Repo context available"),
                "detail": str(entry.get("summary") or entry.get("content") or ""),
                "source": "repo context",
                "derived": True,
            })
        inbox = _product_safe("inbox", lambda: api_inbox(user=_ui_user_id()), {"proposals": [], "findings": []})
        for item in (inbox.get("proposals") or []) + (inbox.get("findings") or []):
            events.append({
                "id": f"review:{item.get('context_id') or item.get('finding_id') or item.get('id')}",
                "time": item.get("updated_at") or item.get("created_at"),
                "kind": "review",
                "title": str(item.get("title") or "Review item"),
                "detail": str(item.get("detail") or item.get("summary") or item.get("content") or ""),
                "source": "inbox",
                "derived": False,
            })
        events.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
        return {
            "live": bool(events),
            "repo": _ui_repo(),
            "items": events[:limit],
            "totals": {
                "events": len(events),
                "digests": sum(1 for item in events if item.get("kind") == "digest"),
                "expansions": sum(1 for item in events if item.get("kind") == "hidden_raw"),
                "evidence": sum(1 for item in events if item.get("kind") == "evidence"),
                "derived": sum(1 for item in events if item.get("derived")),
            },
        }

    @app.get("/api/ui/learnings")
    def api_ui_learnings(limit: int = 120) -> Dict[str, Any]:
        return _product_safe("learnings", lambda: _learning_snapshot(limit=limit), {"items": [], "totals": {}})

    @app.post("/api/ui/learnings/{learning_id}/promote")
    def api_ui_learning_promote(learning_id: str, payload: UiLearningDecisionPayload) -> Dict[str, Any]:
        from dhee.core.learnings import LearningExchange

        try:
            item = LearningExchange().promote(
                learning_id,
                scope=payload.scope or "personal",
                repo=payload.repo or _ui_repo(),
                approved_by=payload.approved_by or "dhee-ui",
            )
            return {"ok": True, "learning": _compact_learning_row(item.to_dict())}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/ui/learnings/{learning_id}/reject")
    def api_ui_learning_reject(learning_id: str, payload: UiLearningDecisionPayload) -> Dict[str, Any]:
        from dhee.core.learnings import LearningExchange

        try:
            item = LearningExchange().reject(learning_id, reason=payload.reason or "rejected in Dhee UI")
            return {"ok": True, "learning": _compact_learning_row(item.to_dict())}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/ui/portability")
    def api_ui_portability() -> Dict[str, Any]:
        try:
            from dhee.protocol import PACK_VERSION
        except Exception:  # noqa: BLE001
            PACK_VERSION = "unknown"  # type: ignore[assignment]
        return {
            "live": True,
            "repo": _ui_repo(),
            "format": ".dheemem",
            "version": PACK_VERSION,
            "counts": _ui_pack_counts(),
            "packs": _latest_dheemem_packs(),
            "contract": [
                "memories",
                "history",
                "vectors",
                "artifacts",
                "provenance",
                "repo context",
                "handoff bootstrap",
            ],
            "trust": {
                "signed": True,
                "local_first": True,
                "clean_import_dry_run": True,
                "no_required_hosted_account": True,
            },
        }

    @app.post("/api/ui/portability/export")
    def api_ui_portability_export(payload: UiPortabilityExportPayload) -> Dict[str, Any]:
        from dhee.cli_config import CONFIG_DIR
        from dhee.protocol import export_pack

        repo = payload.repo or _ui_repo()
        output = payload.output_path
        if not output:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            output = str(Path(_dhee_data_dir_str()) / "exports" / f"dhee-ui-{stamp}.dheemem")
        try:
            memory = _get_memory()
            vector_store = getattr(memory, "vector_store", None)
            db = getattr(memory, "db", None) or _get_db()
            if vector_store is None:
                raise RuntimeError("portable export requires a memory vector store")
            result = export_pack(
                db=db,
                vector_store=vector_store,
                output_path=output,
                user_id=payload.user_id or _ui_user_id(),
                key_dir=CONFIG_DIR,
                repo=repo,
            )
            return {"ok": True, "result": result, "packs": _latest_dheemem_packs()}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/ui/portability/import-dry-run")
    def api_ui_portability_import_dry_run(payload: UiPortabilityImportPayload) -> Dict[str, Any]:
        from dhee.protocol import import_pack

        input_path = _abs_user_path(payload.input_path)
        if not input_path or not Path(input_path).exists():
            raise HTTPException(status_code=400, detail="input_path does not exist")
        try:
            memory = _get_memory()
            vector_store = getattr(memory, "vector_store", None)
            db = getattr(memory, "db", None) or _get_db()
            if vector_store is None:
                raise RuntimeError("portable import dry-run requires a memory vector store")
            result = import_pack(
                db=db,
                vector_store=vector_store,
                input_path=input_path,
                user_id=payload.user_id or _ui_user_id(),
                strategy="dry-run",
                repo=payload.repo or _ui_repo(),
            )
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/integrations")
    def api_integration_set(payload: IntegrationPayload) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                node = svc.set_integration(
                    scope=payload.scope,
                    target_id=payload.target_id,
                    integration_type=payload.type,
                    value=payload.value,
                    metadata=payload.metadata or {},
                )
            finally:
                svc.close()
            return {"ok": True, "node": node}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/team-join")
    def api_team_join(payload: TeamJoinPayload) -> Dict[str, Any]:
        repo_root = _abs_user_path(payload.repo_root) or _ui_repo()
        joined = {
            "org_id": payload.org_id,
            "project_id": payload.project_id,
            "team_id": payload.team_id,
            "role": payload.role,
            "repo_root": repo_root,
            "received_at": _now_iso(),
        }
        try:
            out_dir = Path.home() / ".dhee" / "repo_orgs"
            out_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha1(str(repo_root).encode("utf-8")).hexdigest()[:16]
            (out_dir / f"{digest}.json").write_text(
                json.dumps(joined, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": True,
            "joined": joined,
        }

    @app.post("/api/teams/{team_id}/collaborators")
    def api_team_collaborator_add(
        team_id: str,
        payload: TeamCollaborationPayload,
    ) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                result = svc.add_team_collaborator(
                    team_id=team_id,
                    target_team_id=payload.target_team_id,
                )
            finally:
                svc.close()
            return {"ok": True, **result}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/workspace")
    def api_workspace_set(payload: EnterpriseWorkspacePayload) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                ws = svc.create_workspace(
                    name=payload.name,
                    root_path=_abs_user_path(payload.root_path) if payload.root_path else None,
                    default_branch=payload.default_branch or "main",
                )
            finally:
                svc.close()
            return {"ok": True, "workspace": ws}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/workspace/reset")
    def api_workspace_reset() -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                counts = svc.reset_workspace()
            finally:
                svc.close()
            return {"ok": True, "deleted": counts}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/projects")
    def api_project_create(payload: EnterpriseProjectCreatePayload) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                project = svc.create_project(
                    project_id=payload.project_id,
                    name=payload.name,
                    description=payload.description or "",
                )
            finally:
                svc.close()
            return {"ok": True, "project": project}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.delete("/api/projects/{project_id}")
    def api_project_delete(project_id: str) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                result = svc.delete_project(project_id)
            finally:
                svc.close()
            return {"ok": True, **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/teams")
    def api_project_team_create(
        project_id: str,
        payload: ProjectTeamCreatePayload,
    ) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                team = svc.create_project_team(
                    project_id=project_id,
                    team_id=payload.team_id,
                    name=payload.name,
                    description=payload.description or "",
                )
            finally:
                svc.close()
            return {"ok": True, "team": team}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/teams/{team_id}/folders")
    def api_team_folder_add(
        team_id: str,
        payload: ProjectFolderAddPayload,
    ) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                local = (
                    _abs_user_path(payload.local_path)
                    if payload.local_path else None
                )
                result = svc.add_team_folder(
                    team_id=team_id,
                    local_path=local,
                    repo_url=payload.repo_url,
                    label=payload.label,
                    kind=payload.kind or "folder",
                )
            finally:
                svc.close()
            return {"ok": True, **result}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/folders")
    def api_project_folder_add(
        project_id: str,
        payload: ProjectFolderAddPayload,
    ) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                local = (
                    _abs_user_path(payload.local_path)
                    if payload.local_path else None
                )
                result = svc.add_project_folder(
                    project_id=project_id,
                    local_path=local,
                    repo_url=payload.repo_url,
                    label=payload.label,
                    kind=payload.kind or "folder",
                )
            finally:
                svc.close()
            return {"ok": True, **result}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/extract")
    def api_project_extract(project_id: str) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                result = svc.run_ast_extraction(project_id)
            finally:
                svc.close()
            return {"ok": True, **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/teams/{team_id}/extract")
    def api_team_extract(team_id: str) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                team = svc.store.get_team(svc.org_id, team_id)
                if not team:
                    raise ValueError(f"team_id not found: {team_id}")
                project_id = str(team.get("project_id") or "")
                if not project_id:
                    raise ValueError("team has no project_id")
                result = svc.run_ast_extraction(project_id, team_id=team_id)
            finally:
                svc.close()
            return {"ok": True, **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.delete("/api/folders/{mapping_id}")
    def api_project_folder_remove(mapping_id: str) -> Dict[str, Any]:
        try:
            svc = _enterprise_service()
            try:
                result = svc.remove_project_folder(mapping_id)
            finally:
                svc.close()
            return {"ok": True, **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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


def _abs_user_path(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return os.path.abspath(os.path.expanduser(raw))


def _display_name(value: Optional[str], *, fallback_path: Optional[str] = None, fallback: str = "Workspace") -> str:
    name = str(value or "").strip()
    if name:
        return name
    path = _abs_user_path(fallback_path)
    if path:
        return os.path.basename(path.rstrip(os.sep)) or path
    return fallback


def _get_db():
    global _UI_DB
    from dhee.configs.base import _dhee_data_dir
    from dhee.db.sqlite import SQLiteManager

    db_path = os.environ.get("DHEE_UI_HISTORY_DB") or os.path.join(_dhee_data_dir(), "history.db")
    if _UI_DB is None or getattr(_UI_DB, "db_path", None) != db_path:
        _UI_DB = SQLiteManager(db_path)
    return _UI_DB


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


def _normalize_scope_rules(rules: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in rules or []:
        if not isinstance(item, dict):
            continue
        prefix = str(
            item.get("path_prefix")
            or item.get("pathPrefix")
            or item.get("path")
            or item.get("prefix")
            or ""
        ).strip()
        if not prefix:
            continue
        resolved = _abs_user_path(prefix)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(
            {
                "path_prefix": resolved,
                "label": str(item.get("label") or item.get("name") or "").strip(),
            }
        )
    return normalized


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


_LOCAL_COMMAND_CAVEAT_RE = re.compile(
    r"<local-command-caveat>.*?(?:</local-command-caveat>|do not respond\.?|$)",
    re.IGNORECASE | re.DOTALL,
)


def _clean_session_text(value: Any, *, max_len: int = 160) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if not text:
        return ""
    text = _LOCAL_COMMAND_CAVEAT_RE.sub("", text)
    text = re.sub(
        r"(?is)^caveat:\s*the messages below were generated by the user while running local commands\.\s*do not respond\.?",
        "",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip(" \t\n\r-—:|")
    if not text:
        return ""
    return text[:max_len].rstrip()


def _is_bad_session_title(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return True
    return (
        "local-command-caveat" in raw
        or "do not respond" in raw
        or raw.startswith("caveat: the messages below were generated")
    )


def _fallback_session_title(runtime: Any, cwd: Any = None) -> str:
    normalized = _normalize_runtime(runtime) or str(runtime or "").strip().lower()
    if normalized == "claude-code":
        return "Claude Code session"
    if normalized == "codex":
        return "Codex session"
    folder = os.path.basename(_abs_user_path(cwd).rstrip(os.sep)) if cwd else ""
    return f"{folder} session" if folder else "Agent session"


def _jsonl_tail_lines(path: Path, *, max_bytes: int = _SESSION_LOG_TAIL_BYTES) -> List[str]:
    """Return complete JSONL lines from the end of a potentially huge agent log."""
    try:
        size = path.stat().st_size
        start = max(0, size - max(1024, int(max_bytes)))
        with path.open("rb") as handle:
            if start:
                handle.seek(start)
                handle.readline()
            else:
                handle.seek(0)
            data = handle.read()
    except OSError:
        return []
    return [
        raw.decode("utf-8", errors="ignore")
        for raw in data.splitlines()
        if raw.strip()
    ]


def _session_log_cache_get(kind: str, path: Path, variant: str, stat: os.stat_result) -> Optional[Dict[str, Any]]:
    cached = _SESSION_LOG_PARSE_CACHE.get((kind, str(path), variant))
    if not cached:
        return None
    mtime_ns, size, payload = cached
    if mtime_ns == stat.st_mtime_ns and size == stat.st_size:
        return dict(payload)
    return None


def _session_log_cache_put(kind: str, path: Path, variant: str, stat: os.stat_result, payload: Dict[str, Any]) -> Dict[str, Any]:
    _SESSION_LOG_PARSE_CACHE[(kind, str(path), variant)] = (
        int(stat.st_mtime_ns),
        int(stat.st_size),
        dict(payload),
    )
    if len(_SESSION_LOG_PARSE_CACHE) > 128:
        for key in list(_SESSION_LOG_PARSE_CACHE.keys())[:32]:
            _SESSION_LOG_PARSE_CACHE.pop(key, None)
    return payload


def _session_title(
    value: Any,
    *,
    preview: Any = None,
    runtime: Any = None,
    cwd: Any = None,
    session_id: Any = None,
    max_len: int = 120,
) -> str:
    candidates = [value]
    if preview:
        candidates.append(preview)
    if session_id:
        candidates.append(session_id)
    for candidate in candidates:
        if _is_bad_session_title(candidate):
            continue
        cleaned = _clean_session_text(candidate, max_len=max_len)
        if cleaned and not _is_bad_session_title(cleaned):
            return cleaned
    return _fallback_session_title(runtime, cwd)


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


_RUNTIME_PROCESS_CACHE: Dict[str, Any] = {"at": 0.0, "items": []}


def _process_cwd(pid: Any) -> Optional[str]:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None
    proc_cwd = Path("/proc") / str(pid_int) / "cwd"
    try:
        if proc_cwd.exists():
            return os.path.abspath(os.readlink(proc_cwd))
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid_int), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            check=False,
            timeout=0.6,
        )
    except Exception:
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("n") and len(line) > 1:
            return os.path.abspath(os.path.expanduser(line[1:]))
    return None


def _runtime_processes() -> List[Dict[str, Any]]:
    now = time.time()
    cached_at = float(_RUNTIME_PROCESS_CACHE.get("at") or 0.0)
    if now - cached_at < 5:
        return list(_RUNTIME_PROCESS_CACHE.get("items") or [])
    items: List[Dict[str, Any]] = []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,comm=,args="],
            capture_output=True,
            text=True,
            check=False,
            timeout=0.8,
        )
    except Exception:
        _RUNTIME_PROCESS_CACHE.update({"at": now, "items": []})
        return []
    for line in (result.stdout or "").splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        pid = parts[0]
        comm = parts[1]
        args = parts[2] if len(parts) > 2 else comm
        haystack = f"{comm} {args}".lower()
        lower_args = str(args or "").lower()
        runtime_id = ""
        if os.path.basename(comm).lower() == "claude" or re.search(r"(^|[/\s])claude(\s|$)", haystack):
            runtime_id = "claude-code"
        elif (
            ".app/" not in lower_args
            and "codex computer use" not in lower_args
            and (
                os.path.basename(comm).lower() == "codex"
                or re.search(r"(^|[/\s])codex(\s|$)", haystack)
            )
        ):
            runtime_id = "codex"
        if not runtime_id:
            continue
        items.append(
            {
                "pid": int(pid) if str(pid).isdigit() else pid,
                "runtime_id": runtime_id,
                "command": args,
                "cwd": _process_cwd(pid),
            }
        )
    _RUNTIME_PROCESS_CACHE.update({"at": now, "items": items})
    return list(items)


def _paths_overlap(left: Optional[str], right: Optional[str]) -> bool:
    left_abs = _abs_user_path(left)
    right_abs = _abs_user_path(right)
    if not left_abs or not right_abs:
        return False
    try:
        common = os.path.commonpath([left_abs, right_abs])
    except ValueError:
        return False
    return common in {left_abs, right_abs}


def _runtime_has_process_for_path(runtime_id: str, path: Optional[str]) -> bool:
    candidate = _abs_user_path(path)
    if not candidate:
        return False
    for proc in _runtime_processes():
        if str(proc.get("runtime_id") or "") != runtime_id:
            continue
        if _paths_overlap(proc.get("cwd"), candidate):
            return True
    return False


def _recent_enough(value: Any, *, seconds: int = 1800) -> bool:
    dt = _coerce_datetime(value)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    return 0 <= age.total_seconds() <= seconds


def _workspace_scan_roots(db: Any, extra_paths: Optional[List[str]] = None) -> List[str]:
    roots: List[str] = []
    seen: set[str] = set()

    def add(path: Optional[str]) -> None:
        resolved = _abs_user_path(path)
        if not resolved or resolved in seen:
            return
        seen.add(resolved)
        roots.append(resolved)

    add(_ui_repo())
    for path in extra_paths or []:
        add(path)
    try:
        workspaces = db.list_workspaces(user_id=_ui_user_id(), limit=500)
    except Exception:
        workspaces = []
    for workspace in workspaces:
        add(workspace.get("root_path"))
        workspace_id = str(workspace.get("id") or "")
        try:
            mounts = db.list_workspace_mounts(workspace_id=workspace_id, user_id=_ui_user_id())
        except Exception:
            mounts = []
        for mount in mounts:
            add(mount.get("mount_path") or mount.get("path"))
    return roots


def _mirror_runtime_cache_key(extra_paths: Optional[List[str]]) -> Tuple[str, ...]:
    parts = [_abs_user_path(_ui_repo()) or _ui_repo()]
    for path in extra_paths or []:
        resolved = _abs_user_path(path)
        if resolved:
            parts.append(resolved)
    return tuple(sorted(set(parts)))


def _mirror_runtime_sessions(db: Any, extra_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    cache_key = _mirror_runtime_cache_key(extra_paths)
    now = time.monotonic()
    cached = _MIRROR_RUNTIME_CACHE.get(cache_key)
    if cached and now - cached[0] <= _MIRROR_RUNTIME_CACHE_TTL_SECONDS:
        return cached[1]

    with _MIRROR_RUNTIME_LOCK:
        now = time.monotonic()
        cached = _MIRROR_RUNTIME_CACHE.get(cache_key)
        if cached and now - cached[0] <= _MIRROR_RUNTIME_CACHE_TTL_SECONDS:
            return cached[1]
        result = _mirror_runtime_sessions_uncached(db, extra_paths=extra_paths)
        _MIRROR_RUNTIME_CACHE[cache_key] = (time.monotonic(), result)
        if len(_MIRROR_RUNTIME_CACHE) > 32:
            for key in list(_MIRROR_RUNTIME_CACHE.keys())[:8]:
                _MIRROR_RUNTIME_CACHE.pop(key, None)
        return result


def _mirror_runtime_sessions_uncached(db: Any, extra_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    repo = _ui_repo()
    project, default_workspace = _ensure_default_project_workspace(db, repo)
    mirrored: List[Dict[str, Any]] = []
    seen_sessions: set[tuple[str, str]] = set()

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
        repo_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        dedupe_key = (runtime_id, native_session_id)
        if dedupe_key in seen_sessions:
            return next((item for item in mirrored if item.get("runtime_id") == runtime_id and item.get("native_session_id") == native_session_id), {})
        seen_sessions.add(dedupe_key)
        task_repo = _abs_user_path(repo_hint or cwd or repo) or repo
        workspace = _resolve_workspace_for_path(db, path=cwd, project_id=None) or default_workspace
        resolved_project = _resolve_workspace_project_for_path(
            db,
            workspace_id=str(workspace.get("id") or ""),
            path=cwd or task_repo,
        ) or _ensure_unassigned_workspace_project(db, workspace)
        session_id = _agent_session_id(runtime_id, native_session_id)
        task_id = _session_task_id(runtime_id, native_session_id)
        task_status = "active" if is_current or state == "active" else "paused"
        metadata_payload = metadata or {}
        clean_title = _session_title(
            title,
            preview=metadata_payload.get("preview"),
            runtime=runtime_id,
            cwd=cwd,
            session_id=native_session_id,
        )
        task = db.upsert_shared_task(
            {
                "id": task_id,
                "user_id": _ui_user_id(),
                "project_id": resolved_project["id"],
                "repo": task_repo,
                "workspace_id": workspace["id"],
                "folder_path": ".",
                "session_id": session_id,
                "thread_id": native_session_id,
                "runtime_id": runtime_id,
                "native_session_id": native_session_id,
                "title": clean_title,
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
                    **metadata_payload,
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
                "title": clean_title,
                "state": state,
                "model": model,
                "cwd": cwd,
                "rollout_path": rollout_path,
                "permission_mode": permission_mode,
                "started_at": started_at,
                "updated_at": updated_at or _now_iso(),
                "metadata": metadata_payload,
            }
        )
        mirrored.append(session)
        return session

    for scan_root in _workspace_scan_roots(db, extra_paths=extra_paths):
        for thread in _repo_codex_threads(scan_root, limit=_RUNTIME_MIRROR_CODEX_LIMIT):
            thread_id = str(thread.get("id") or "").strip()
            if not thread_id:
                continue
            thread_cwd = str(thread.get("cwd") or scan_root)
            is_current = bool(thread.get("isCurrent"))
            mirror_one(
                "codex",
                thread_id,
                title=str(thread.get("title") or "Untitled Codex session"),
                cwd=thread_cwd,
                model=thread.get("model"),
                state="active" if is_current else str(thread.get("state") or "recent"),
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
                    "is_current": is_current,
                },
                is_current=is_current,
                repo_hint=scan_root,
            )

        for claude_session in _find_claude_sessions(scan_root, limit=_RUNTIME_MIRROR_CLAUDE_LIMIT):
            native_id = str(claude_session.get("id") or "claude-local")
            if not native_id:
                continue
            claude_cwd = str(claude_session.get("cwd") or scan_root)
            is_current = str(claude_session.get("state") or "") == "active"
            mirror_one(
                "claude-code",
                native_id,
                title=str(claude_session.get("title") or "Claude Code session"),
                cwd=claude_cwd,
                model=claude_session.get("model"),
                state=str(claude_session.get("state") or "recent"),
                started_at=claude_session.get("startedAt"),
                updated_at=claude_session.get("updatedAt"),
                permission_mode=str(claude_session.get("permissionMode") or "native"),
                metadata={
                    "version": claude_session.get("version"),
                    "entrypoint": claude_session.get("entrypoint"),
                    "note": claude_session.get("note"),
                    "messages": claude_session.get("messages") or [],
                    "recent_tools": claude_session.get("recentTools") or [],
                    "plan": [],
                    "touched_files": claude_session.get("touchedFiles") or [],
                    "rate_limits": {},
                    "token_usage": claude_session.get("tokenUsage") or {},
                    "last_token_usage": claude_session.get("lastTokenUsage") or {},
                    "token_usage_complete": claude_session.get("tokenUsageComplete"),
                    "preview": claude_session.get("preview"),
                    "is_current": is_current,
                    "pid": claude_session.get("pid"),
                },
                is_current=is_current,
                repo_hint=scan_root,
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


def _router_payg_pricing(runtime: Any, model: Any) -> Dict[str, Any]:
    """Return PAYG input-token pricing used to value avoided raw reads."""

    haystack = re.sub(
        r"[\s_]+",
        "-",
        f"{runtime or ''} {model or ''}".strip().lower(),
    )

    def pricing(
        provider: str,
        family: str,
        input_rate: float,
        cached_rate: Optional[float],
        output_rate: Optional[float],
        source: str,
        note: str,
    ) -> Dict[str, Any]:
        return {
            "provider": provider,
            "model_family": family,
            "input_cost_per_million": input_rate,
            "cached_input_cost_per_million": cached_rate,
            "output_cost_per_million": output_rate,
            "currency": "USD",
            "unit": "1M input tokens",
            "source": source,
            "note": note,
        }

    anthropic_source = "https://www.anthropic.com/api"
    openai_source = "https://openai.com/api/pricing/"
    codex_source = "https://developers.openai.com/api/docs/pricing"

    if "claude" in haystack or "anthropic" in haystack:
        if "haiku-3" in haystack or "3-haiku" in haystack:
            return pricing(
                "anthropic",
                "Claude 3 Haiku",
                0.25,
                None,
                1.25,
                anthropic_source,
                "Input-token estimate for avoided raw context.",
            )
        if "3.5-haiku" in haystack or "3-5-haiku" in haystack:
            return pricing(
                "anthropic",
                "Claude 3.5 Haiku",
                0.80,
                None,
                4.0,
                anthropic_source,
                "Input-token estimate for avoided raw context.",
            )
        if "haiku" in haystack:
            return pricing(
                "anthropic",
                "Claude Haiku 4.5",
                1.0,
                0.10,
                5.0,
                anthropic_source,
                "Input-token estimate for avoided raw context.",
            )
        if "sonnet" in haystack:
            return pricing(
                "anthropic",
                "Claude Sonnet 4.6",
                3.0,
                0.30,
                15.0,
                anthropic_source,
                "Input-token estimate for avoided raw context.",
            )
        if "opus-4.1" in haystack or "opus-4-1" in haystack:
            return pricing(
                "anthropic",
                "Claude Opus 4.1",
                15.0,
                None,
                75.0,
                anthropic_source,
                "Input-token estimate for avoided raw context.",
            )
        if "opus" in haystack:
            return pricing(
                "anthropic",
                "Claude Opus 4.7",
                5.0,
                0.50,
                25.0,
                anthropic_source,
                "Input-token estimate for avoided raw context.",
            )
        return pricing(
            "anthropic",
            "Unpriced Claude model",
            0.0,
            None,
            None,
            anthropic_source,
            "Claude runtime was captured, but the exact model was not mapped to an official price.",
        )

    if "gpt-5.5" in haystack or "gpt-5-5" in haystack:
        return pricing(
            "openai",
            "GPT-5.5",
            5.0,
            0.50,
            30.0,
            openai_source,
            "Input-token estimate for avoided raw context.",
        )
    if "gpt-5.4-mini" in haystack or "gpt-5-4-mini" in haystack:
        return pricing(
            "openai",
            "GPT-5.4 mini",
            0.75,
            0.075,
            4.50,
            openai_source,
            "Input-token estimate for avoided raw context.",
        )
    if "gpt-5.4" in haystack or "gpt-5-4" in haystack:
        return pricing(
            "openai",
            "GPT-5.4",
            2.50,
            0.25,
            15.0,
            openai_source,
            "Input-token estimate for avoided raw context.",
        )
    if (
        "gpt-5.3-codex" in haystack
        or "gpt-5-3-codex" in haystack
        or "gpt-5.2-codex" in haystack
        or "gpt-5-2-codex" in haystack
    ):
        return pricing(
            "openai",
            "GPT-5.3-Codex",
            1.75,
            0.175,
            14.0,
            codex_source,
            "Input-token estimate for avoided raw context.",
        )
    if "gpt-5.2" in haystack or "gpt-5-2" in haystack:
        return pricing(
            "openai",
            "GPT-5.2",
            1.75,
            0.175,
            14.0,
            openai_source,
            "Input-token estimate for avoided raw context.",
        )
    if "gpt-5.1" in haystack or "gpt-5-1" in haystack or re.search(r"\bgpt-5\b", haystack):
        return pricing(
            "openai",
            "GPT-5",
            1.25,
            0.125,
            10.0,
            openai_source,
            "Input-token estimate for avoided raw context.",
        )
    if "gpt-4.1" in haystack or "gpt-4-1" in haystack:
        return pricing(
            "openai",
            "GPT-4.1",
            2.0,
            0.50,
            8.0,
            openai_source,
            "Input-token estimate for avoided raw context.",
        )
    if "gpt-4o" in haystack:
        return pricing(
            "openai",
            "GPT-4o",
            2.50,
            1.25,
            10.0,
            openai_source,
            "Input-token estimate for avoided raw context.",
        )
    if "codex" in haystack:
        return pricing(
            "openai",
            "Unpriced Codex model",
            0.0,
            None,
            None,
            codex_source,
            "Codex runtime was captured, but the exact model was not mapped to an official price.",
        )
    if "gpt" in haystack or "openai" in haystack:
        return pricing(
            "openai",
            "Unpriced OpenAI model",
            0.0,
            None,
            None,
            openai_source,
            "OpenAI runtime was captured, but the exact model was not mapped to an official price.",
        )

    return pricing(
        "unknown",
        "Unpriced model",
        0.0,
        None,
        None,
        "",
        "Provider/model was not captured, so Dhee does not estimate dollar savings.",
    )


def _router_monthly_budget_usd() -> float:
    """Maximum monthly dollar value Dhee should claim as realised savings.

    The default matches the product assumption discussed in the UI work:
    $20 base + $20 Claude + $20 Codex = $60/month. Teams can override this
    with DHEE_MONTHLY_AI_BUDGET_USD. Provider-specific env vars are also
    accepted; if any are set, their sum wins.
    """

    def _env_float(name: str) -> Optional[float]:
        raw = os.environ.get(name)
        if raw in (None, ""):
            return None
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None

    provider_values = [
        _env_float("DHEE_BASE_MONTHLY_BUDGET_USD"),
        _env_float("DHEE_CLAUDE_MONTHLY_BUDGET_USD"),
        _env_float("DHEE_CODEX_MONTHLY_BUDGET_USD"),
    ]
    if any(value is not None for value in provider_values):
        return round(sum(value or 0.0 for value in provider_values), 6)
    return round(_env_float("DHEE_MONTHLY_AI_BUDGET_USD") or 60.0, 6)


def _router_budget_payload() -> Dict[str, Any]:
    monthly = _router_monthly_budget_usd()
    return {
        "currency": "USD",
        "monthly_budget_usd": monthly,
        "daily_budget_usd": round(monthly / 30.0, 6),
        "weekly_budget_usd": round(monthly * 7.0 / 30.0, 6),
        "yearly_budget_usd": round(monthly * 12.0, 6),
        "basis": "configured monthly AI budget",
        "note": (
            "Realised saved dollars are capped by the user's configured paid "
            "budget. Avoided API value is shown separately and can exceed this "
            "only as an estimate, not as claimed cash saved."
        ),
    }


def _budget_cap_usd(value: float, *, period: str = "month") -> float:
    budget = _router_budget_payload()
    key = {
        "day": "daily_budget_usd",
        "week": "weekly_budget_usd",
        "month": "monthly_budget_usd",
        "year": "yearly_budget_usd",
    }.get(period, "monthly_budget_usd")
    cap = float(budget.get(key) or 0.0)
    return round(min(max(0.0, float(value or 0.0)), cap), 6)


def _context_ids_from_meta(meta: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in (
        "context_id",
        "context_ids",
        "selected_context_ids",
        "injected_context_ids",
        "dhee_context_ids",
    ):
        value = meta.get(key)
        if not value:
            continue
        if isinstance(value, str):
            bits = re.split(r"[\s,]+", value)
        elif isinstance(value, (list, tuple, set)):
            bits = [str(v) for v in value]
        else:
            bits = [str(value)]
        out.extend(bit.strip() for bit in bits if bit and bit.strip())
    return sorted(set(out))


def _metadata_int(*values: Any) -> int:
    for value in values:
        try:
            if value in (None, ""):
                continue
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            continue
    return 0


def _context_proven_router_savings(context_ids: set[str]) -> Dict[str, Dict[str, Any]]:
    if not context_ids:
        return {}
    from dhee.router import ptr_store as _ptr
    from dhee.router import stats as _rstats

    out: Dict[str, Dict[str, Any]] = {
        cid: {"tokens": 0, "api_value_usd": 0.0, "calls": 0}
        for cid in context_ids
    }
    try:
        ptr_root = _ptr._root()
    except Exception:  # noqa: BLE001
        return out
    if not ptr_root.exists():
        return out
    chars_per_token = float(getattr(_rstats, "CHARS_PER_TOKEN", 4))
    for sdir in ptr_root.iterdir():
        if not sdir.is_dir():
            continue
        for meta_file in sdir.glob("*.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            matched = [cid for cid in _context_ids_from_meta(meta) if cid in context_ids]
            if not matched:
                continue
            tokens = int(_rstats._stored_chars(meta, meta_file) / chars_per_token) if chars_per_token else 0
            if tokens <= 0:
                continue
            pricing = _router_payg_pricing(
                meta.get("harness") or meta.get("agent_id"),
                meta.get("model"),
            )
            api_value = tokens * float(pricing.get("input_cost_per_million") or 0.0) / 1_000_000
            share_tokens = int(tokens / max(1, len(matched)))
            share_value = api_value / max(1, len(matched))
            for cid in matched:
                row = out.setdefault(cid, {"tokens": 0, "api_value_usd": 0.0, "calls": 0})
                row["tokens"] += share_tokens
                row["api_value_usd"] += share_value
                row["calls"] += 1
    return out


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


def _claude_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _claude_log_details(path: Path, *, fallback_cwd: str) -> Dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        stat = None
    if stat is not None:
        cached = _session_log_cache_get("claude", path, fallback_cwd, stat)
        if cached is not None:
            return cached

    messages: deque[Dict[str, Any]] = deque(maxlen=8)
    recent_tools: deque[str] = deque(maxlen=8)
    touched_files: set[str] = set()
    session_id = path.stem
    cwd = fallback_cwd
    version = None
    model = None
    started_at = None
    updated_at = _iso_or_none(stat.st_mtime) if stat is not None else None
    first_user = ""
    last_user = ""
    last_assistant = ""
    permission_mode = "native"
    usage_seen: set[str] = set()
    token_usage: Dict[str, int] = {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    }
    last_token_usage: Dict[str, int] = {}

    def absorb_usage(raw_usage: Any, usage_key: str) -> None:
        nonlocal last_token_usage
        if not isinstance(raw_usage, dict) or not usage_key or usage_key in usage_seen:
            return
        usage_seen.add(usage_key)
        current = {
            "input_tokens": int(raw_usage.get("input_tokens") or 0),
            "cache_creation_input_tokens": int(raw_usage.get("cache_creation_input_tokens") or 0),
            "cache_read_input_tokens": int(raw_usage.get("cache_read_input_tokens") or 0),
            "output_tokens": int(raw_usage.get("output_tokens") or 0),
        }
        for key, value in current.items():
            token_usage[key] = token_usage.get(key, 0) + value
        last_token_usage = current

    for line in _jsonl_tail_lines(path):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = str(item.get("sessionId") or session_id)
        cwd = str(item.get("cwd") or cwd or fallback_cwd)
        version = item.get("version") or version
        timestamp = item.get("timestamp")
        if timestamp and started_at is None:
            started_at = _iso_or_none(timestamp)
        if timestamp:
            updated_at = _iso_or_none(timestamp) or updated_at
        if item.get("type") == "permission-mode":
            raw_mode = str(item.get("permissionMode") or item.get("mode") or "").strip()
            if raw_mode:
                permission_mode = _normalize_permission_mode("claude-code", raw_mode)
        message = item.get("message") or {}
        if isinstance(message, dict):
            role = str(message.get("role") or item.get("type") or "").strip()
            model = message.get("model") or model
            content = message.get("content")
            absorb_usage(
                message.get("usage"),
                str(message.get("id") or item.get("requestId") or item.get("uuid") or timestamp or ""),
            )
            text = _claude_message_text(content)
            if text and role in {"user", "assistant"}:
                messages.append(
                    {
                        "role": role,
                        "content": text,
                        "timestamp": timestamp,
                    }
                )
                if role == "user":
                    if not first_user:
                        first_user = text
                    last_user = text
                elif role == "assistant":
                    last_assistant = text
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_name = str(block.get("name") or "").strip()
                    if tool_name:
                        recent_tools.append(tool_name)
                    tool_input = block.get("input") or {}
                    if isinstance(tool_input, dict):
                        file_path = str(
                            tool_input.get("file_path")
                            or tool_input.get("path")
                            or ""
                        ).strip()
                        if file_path:
                            touched_files.add(_abs_user_path(file_path) or file_path)

    preview = last_assistant or last_user
    title = _session_title(
        first_user or last_user or path.stem,
        preview=last_user or preview,
        runtime="claude-code",
        cwd=cwd or fallback_cwd,
        session_id=session_id,
    )
    result = {
        "id": session_id,
        "cwd": cwd or fallback_cwd,
        "title": title,
        "model": model,
        "startedAt": started_at,
        "updatedAt": updated_at,
        "version": version,
        "permissionMode": permission_mode,
        "messages": list(messages),
        "recentTools": list(recent_tools),
        "touchedFiles": sorted(touched_files)[:80],
        "preview": preview[:600],
        "logPath": str(path),
        "tokenUsage": token_usage,
        "lastTokenUsage": last_token_usage,
        "tokenUsageComplete": bool(stat is not None and stat.st_size <= _SESSION_LOG_TAIL_BYTES),
    }
    if stat is not None:
        return _session_log_cache_put("claude", path, fallback_cwd, stat, result)
    return result


def _claude_logs_for_repo(repo: str, *, limit: int = 6) -> List[Path]:
    try:
        from dhee.core.log_parser import _escape_path
    except Exception:
        escaped = repo.replace("/", "-").replace("\\", "-")
    else:
        escaped = _escape_path(repo)
    root = Path.home() / ".claude" / "projects" / escaped
    if not root.is_dir():
        return []
    try:
        files = [path for path in root.iterdir() if path.is_file() and path.suffix == ".jsonl"]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, int(limit))]


def _find_claude_sessions(repo: str, limit: int = 6) -> List[Dict[str, Any]]:
    repo_abs = _abs_user_path(repo)
    sessions: Dict[str, Dict[str, Any]] = {}

    root = Path.home() / ".claude" / "sessions"
    if root.exists():
        for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            cwd = _abs_user_path(data.get("cwd"))
            pid = data.get("pid")
            process_cwd = _process_cwd(pid) if _pid_alive(pid) else None
            effective_cwd = cwd or process_cwd or repo_abs
            if repo_abs and effective_cwd and not _paths_overlap(effective_cwd, repo_abs):
                continue
            session_id = str(data.get("sessionId") or path.stem)
            if not session_id:
                continue
            sessions[session_id] = {
                "id": session_id,
                "cwd": effective_cwd,
                "pid": pid,
                "startedAt": _iso_or_none(data.get("startedAt")),
                "updatedAt": _iso_or_none(path.stat().st_mtime),
                "state": "active" if _pid_alive(pid) else "stale",
                "version": data.get("version"),
                "entrypoint": data.get("entrypoint"),
                "title": _session_title(
                    data.get("title"),
                    preview=data.get("preview") or data.get("lastUser") or data.get("last_user"),
                    runtime="claude-code",
                    cwd=effective_cwd,
                    session_id=session_id,
                ),
                "permissionMode": _normalize_permission_mode("claude-code", data.get("permissionMode") or data.get("permission_mode")),
                "note": "Claude Code session discovered from the local session registry.",
            }

    for log_path in _claude_logs_for_repo(repo_abs, limit=limit):
        details = _claude_log_details(log_path, fallback_cwd=repo_abs)
        session_id = str(details.get("id") or log_path.stem)
        if not session_id:
            continue
        existing = sessions.get(session_id, {})
        cwd = _abs_user_path(details.get("cwd") or existing.get("cwd") or repo_abs)
        active = bool(existing.get("state") == "active")
        state = "active" if active else ("recent" if _recent_enough(details.get("updatedAt"), seconds=86_400) else "stale")
        sessions[session_id] = {
            **existing,
            **details,
            "id": session_id,
            "cwd": cwd,
            "pid": existing.get("pid"),
            "state": state,
            "entrypoint": existing.get("entrypoint") or "cli",
            "note": "Claude Code conversation log mirrored from ~/.claude/projects.",
        }

    return sorted(
        sessions.values(),
        key=lambda item: _coerce_datetime(item.get("updatedAt")) or datetime.fromtimestamp(0, tz=timezone.utc),
        reverse=True,
    )[: max(1, int(limit))]


def _find_claude_session(repo: str) -> Optional[Dict[str, Any]]:
    sessions = _find_claude_sessions(repo, limit=1)
    return sessions[0] if sessions else None


def _find_codex_session(repo: str) -> Optional[Dict[str, Any]]:
    threads = _repo_codex_threads(repo, limit=1)
    if not threads:
        return None
    thread = threads[0]
    return {
        "id": str(thread.get("id") or ""),
        "cwd": thread.get("cwd"),
        "title": thread.get("title"),
        "model": thread.get("model"),
        "rolloutPath": thread.get("rolloutPath"),
        "startedAt": thread.get("startedAt"),
        "updatedAt": thread.get("updatedAt"),
        "state": "active" if thread.get("isCurrent") else str(thread.get("state") or "recent"),
        "note": (
            "Codex local state shows the most recent thread for this repo; "
            "recently updated threads are treated as live for the UI."
        ),
    }


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
    title = _session_title(
        session.get("title"),
        preview=metadata.get("preview"),
        runtime=session.get("runtime_id"),
        cwd=session.get("cwd"),
        session_id=session.get("native_session_id") or session.get("id"),
    )
    return {
        "id": str(session.get("id") or ""),
        "nativeSessionId": str(session.get("native_session_id") or ""),
        "projectId": session.get("project_id"),
        "workspaceId": session.get("workspace_id"),
        "taskId": session.get("task_id"),
        "runtime": session.get("runtime_id"),
        "title": title,
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
        "tokenUsage": dict(metadata.get("token_usage") or {}),
        "lastTokenUsage": dict(metadata.get("last_token_usage") or {}),
        "tokenUsageComplete": metadata.get("token_usage_complete"),
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
        from dhee.cli_config import get_memory_instance

        memory = get_memory_instance(None)
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
                    "label": _session_title(
                        session.get("title"),
                        preview=session.get("preview"),
                        runtime=session.get("runtime"),
                        cwd=session.get("cwd"),
                        session_id=session.get("nativeSessionId") or session_id,
                    ),
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
    repo_abs = _abs_user_path(repo)
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
            (f"{repo_abs}%", limit),
        ).fetchall()
    finally:
        conn.close()
    items: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        data = dict(row)
        cwd = _abs_user_path(data.get("cwd") or repo_abs)
        if repo_abs and cwd and not _path_matches_repo(cwd, repo_abs):
            continue
        rollout_path = str(data.get("rollout_path") or "")
        rollout = _parse_codex_rollout(Path(rollout_path), repo=repo_abs)
        updated_at = _iso_or_none(data.get("updated_at_ms") or data.get("updated_at"))
        started_at = _iso_or_none(data.get("created_at_ms") or data.get("created_at"))
        is_current = index == 0 and (
            _recent_enough(updated_at, seconds=1800) or _runtime_has_process_for_path("codex", cwd)
        )
        items.append(
            {
                "id": str(data.get("id") or f"thread-{index}"),
                "title": _session_title(
                    data.get("title") or "Untitled Codex session",
                    preview=rollout.get("preview"),
                    runtime="codex",
                    cwd=cwd,
                    session_id=data.get("id") or f"thread-{index}",
                ),
                "cwd": cwd,
                "model": data.get("model"),
                "startedAt": started_at,
                "updatedAt": updated_at,
                "updatedAtLabel": _format_ui_clock(updated_at),
                "rolloutPath": rollout_path,
                "isCurrent": is_current,
                "state": "active" if is_current else ("recent" if _recent_enough(updated_at, seconds=86_400) else "stale"),
                **rollout,
            }
        )
    return items


def _parse_codex_rollout(path: Path, *, repo: str) -> Dict[str, Any]:
    empty = {
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
    if not path.exists():
        return empty
    try:
        stat = path.stat()
    except OSError:
        return empty
    cached = _session_log_cache_get("codex", path, repo, stat)
    if cached is not None:
        return cached
    messages: deque[Dict[str, Any]] = deque(maxlen=8)
    recent_tools: deque[str] = deque(maxlen=8)
    touched_files: set[str] = set()
    latest_plan: List[Dict[str, Any]] = []
    latest_rate_limits: Dict[str, Any] = {}
    latest_token_usage: Dict[str, Any] = {}
    latest_last_token_usage: Dict[str, Any] = {}
    context_window: Optional[int] = None
    for line in _jsonl_tail_lines(path):
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
    result = {
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
    return _session_log_cache_put("codex", path, repo, stat, result)


def _router_codex_live_usage_from_thread(thread: Dict[str, Any]) -> Dict[str, Any]:
    total = dict(thread.get("tokenUsage") or {})
    last = dict(thread.get("lastTokenUsage") or {})

    def _coerce_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    return {
        "available": bool(total or last),
        "source": "codex token_count event",
        "exact": True,
        "input_tokens": _coerce_int(total.get("input_tokens")),
        "cached_input_tokens": _coerce_int(total.get("cached_input_tokens")),
        "output_tokens": _coerce_int(total.get("output_tokens")),
        "reasoning_output_tokens": _coerce_int(total.get("reasoning_output_tokens")),
        "total_tokens": _coerce_int(total.get("total_tokens")),
        "last_turn_tokens": _coerce_int(last.get("total_tokens")),
        "last_turn_input_tokens": _coerce_int(last.get("input_tokens")),
        "last_turn_cached_input_tokens": _coerce_int(last.get("cached_input_tokens")),
        "last_turn_output_tokens": _coerce_int(last.get("output_tokens")),
        "context_window": thread.get("contextWindow"),
        "updated_at": thread.get("updatedAt"),
        "note": "Actual native token telemetry reported by the local Codex session.",
    }


def _router_claude_live_usage_from_summary(summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    total = dict(summary.get("tokenUsage") or {})
    last = dict(summary.get("lastTokenUsage") or {})
    if not total and not last:
        return None

    input_tokens = int(total.get("input_tokens") or 0)
    cache_creation = int(total.get("cache_creation_input_tokens") or 0)
    cache_read = int(total.get("cache_read_input_tokens") or 0)
    output_tokens = int(total.get("output_tokens") or 0)
    last_input = int(last.get("input_tokens") or 0)
    last_cache_creation = int(last.get("cache_creation_input_tokens") or 0)
    last_cache_read = int(last.get("cache_read_input_tokens") or 0)
    last_output = int(last.get("output_tokens") or 0)
    complete = bool(summary.get("tokenUsageComplete"))

    return {
        "available": True,
        "source": "claude-code transcript usage",
        "exact": complete,
        "input_tokens": input_tokens + cache_creation + cache_read,
        "cached_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": None,
        "total_tokens": input_tokens + cache_creation + cache_read + output_tokens,
        "last_turn_tokens": last_input + last_cache_creation + last_cache_read + last_output,
        "last_turn_input_tokens": last_input + last_cache_creation + last_cache_read,
        "last_turn_cached_input_tokens": last_cache_read,
        "last_turn_output_tokens": last_output,
        "context_window": None,
        "updated_at": summary.get("updatedAt"),
        "note": (
            "Actual Claude Code token usage parsed from the local transcript."
            if complete
            else "Recent Claude Code token usage parsed from the local transcript tail."
        ),
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
