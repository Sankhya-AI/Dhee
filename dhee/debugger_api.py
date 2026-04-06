"""FastAPI service for the Cognitive Debugger."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from dhee.core.belief import (
    BeliefFreshnessStatus,
    BeliefLifecycleStatus,
    BeliefNode,
    BeliefProtectionLevel,
    BeliefStatus,
    BeliefStore,
)

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
except ImportError as exc:  # pragma: no cover - import handled at runtime
    FastAPI = None  # type: ignore[assignment]
    HTTPException = RuntimeError  # type: ignore[assignment]
    Query = None  # type: ignore[assignment]
    _FASTAPI_IMPORT_ERROR = exc
else:
    _FASTAPI_IMPORT_ERROR = None


def _default_cognition_dir() -> str:
    return (
        os.environ.get("DHEE_DEBUGGER_DATA_DIR")
        or os.environ.get("DHEE_BUDDHI_DIR")
        or os.path.join(os.path.expanduser("~"), ".dhee", "buddhi")
    )


def _resolve_belief_dir(path: Optional[str]) -> str:
    root = path or _default_cognition_dir()
    if os.path.basename(os.path.normpath(root)) == "beliefs":
        return root
    return os.path.join(root, "beliefs")


def _serialize_belief(belief: BeliefNode) -> Dict[str, Any]:
    source_memory_ids = list(belief.source_memory_ids or [])
    source_episode_ids = list(belief.source_episode_ids or [])
    tags = list(belief.tags or [])
    return {
        "id": belief.id,
        "claim": belief.claim,
        "domain": belief.domain,
        "confidence": belief.confidence,
        "status": belief.status.value,
        "truth_status": belief.truth_status.value,
        "freshness_status": belief.freshness_status.value,
        "lifecycle_status": belief.lifecycle_status.value,
        "protection_level": belief.protection_level.value,
        "origin": belief.origin,
        "successor_id": belief.successor_id,
        "created_at": belief.created_at,
        "updated_at": belief.updated_at,
        "source_memory_ids": source_memory_ids,
        "source_episode_ids": source_episode_ids,
        "tags": tags,
        "source_count": len(set(source_memory_ids + source_episode_ids)),
        "contradiction_count": len(belief.contradicts),
        "evidence_for": belief.supporting_evidence_count,
        "evidence_against": belief.contradicting_evidence_count,
        "stability": belief.stability,
        "is_listable": belief.is_listable(),
        "has_contradictions": bool(belief.contradicts),
    }


def _resolve_user_id(store: BeliefStore, requested: Optional[str]) -> str:
    if requested:
        return requested
    users = store.list_user_ids()
    return users[0] if users else "default"


def _recommended_resolution(left: BeliefNode, right: BeliefNode) -> str:
    if left.protection_level == BeliefProtectionLevel.PINNED and right.protection_level != BeliefProtectionLevel.PINNED:
        return "keep_a"
    if right.protection_level == BeliefProtectionLevel.PINNED and left.protection_level != BeliefProtectionLevel.PINNED:
        return "keep_b"
    if abs(left.confidence - right.confidence) >= 0.15:
        return "keep_a" if left.confidence > right.confidence else "keep_b"
    overlap = len(set(left._claim_keywords) & set(right._claim_keywords))
    return "merge" if overlap >= 3 else "mark_both_stale"


class ActionRequest(BaseModel):
    actor: str = "debugger"
    reason: str = ""


class CorrectBeliefRequest(ActionRequest):
    new_claim: str = Field(..., min_length=1)


class PinBeliefRequest(ActionRequest):
    pinned: bool = True


class MergeBeliefsRequest(ActionRequest):
    survivor_id: str
    loser_id: str


@lru_cache(maxsize=4)
def _get_store_cached(belief_dir: str) -> BeliefStore:
    return BeliefStore(data_dir=belief_dir)


def create_app(data_dir: Optional[str] = None) -> FastAPI:
    if FastAPI is None:  # pragma: no cover - runtime-only path
        raise RuntimeError(
            "FastAPI is required for the Cognitive Debugger API. "
            "Install Dhee with the [api] extra."
        ) from _FASTAPI_IMPORT_ERROR

    belief_dir = _resolve_belief_dir(data_dir)
    app = FastAPI(title="Dhee Cognitive Debugger API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_store() -> BeliefStore:
        store = _get_store_cached(belief_dir)
        store.reload()
        return store

    @app.get("/healthz")
    def healthz() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/debugger/overview")
    def get_overview(user_id: Optional[str] = None) -> Dict[str, Any]:
        store = get_store()
        selected_user = _resolve_user_id(store, user_id)
        beliefs, _ = store.query_beliefs(user_id=selected_user, include_inactive=True, page_size=10000)
        contradictions = store.get_contradictions(selected_user)
        return {
            "user_id": selected_user,
            "users": store.list_user_ids(),
            "counts": {
                "active": sum(1 for belief in beliefs if belief.lifecycle_status == BeliefLifecycleStatus.ACTIVE),
                "stale": sum(1 for belief in beliefs if belief.freshness_status == BeliefFreshnessStatus.STALE),
                "contradicted": sum(1 for belief in beliefs if belief.contradicts and belief.lifecycle_status == BeliefLifecycleStatus.ACTIVE),
                "pinned": sum(1 for belief in beliefs if belief.protection_level == BeliefProtectionLevel.PINNED),
                "tombstoned": sum(1 for belief in beliefs if belief.lifecycle_status == BeliefLifecycleStatus.TOMBSTONED),
            },
            "contradictions": len(contradictions),
            "influence": store.get_influence_stats(selected_user),
        }

    @app.get("/api/debugger/beliefs")
    def list_beliefs(
        user_id: Optional[str] = None,
        search: Optional[str] = None,
        domain: Optional[str] = None,
        truth_status: Optional[str] = None,
        freshness_status: Optional[str] = None,
        lifecycle_status: Optional[str] = None,
        protection_level: Optional[str] = None,
        origin: Optional[str] = None,
        min_confidence: float = 0.0,
        max_confidence: float = 1.0,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        store = get_store()
        selected_user = _resolve_user_id(store, user_id)
        beliefs, total = store.query_beliefs(
            user_id=selected_user,
            search=search,
            domain=domain,
            truth_status=truth_status,
            freshness_status=freshness_status,
            lifecycle_status=lifecycle_status,
            protection_level=protection_level,
            origin=origin,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            page=page,
            page_size=page_size,
            include_inactive=True,
        )
        return {
            "user_id": selected_user,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [_serialize_belief(belief) for belief in beliefs],
        }

    @app.get("/api/debugger/beliefs/{belief_id}")
    def get_belief_detail(belief_id: str) -> Dict[str, Any]:
        store = get_store()
        belief = store.get_belief(belief_id, include_inactive=True)
        if not belief:
            raise HTTPException(status_code=404, detail="Belief not found")
        return _serialize_belief(belief)

    @app.get("/api/debugger/beliefs/{belief_id}/evidence")
    def get_belief_evidence(belief_id: str, limit: int = 200) -> Dict[str, Any]:
        store = get_store()
        belief = store.get_belief(belief_id, include_inactive=True)
        if not belief:
            raise HTTPException(status_code=404, detail="Belief not found")
        return {"belief_id": belief_id, "items": store.get_belief_evidence(belief_id, limit=limit)}

    @app.get("/api/debugger/beliefs/{belief_id}/history")
    def get_belief_history(belief_id: str, limit: int = 100) -> Dict[str, Any]:
        store = get_store()
        belief = store.get_belief(belief_id, include_inactive=True)
        if not belief:
            raise HTTPException(status_code=404, detail="Belief not found")
        return {"belief_id": belief_id, "items": store.get_belief_history(belief_id, limit=limit)}

    @app.get("/api/debugger/beliefs/{belief_id}/impact")
    def get_belief_impact(belief_id: str, limit: int = 100) -> Dict[str, Any]:
        store = get_store()
        belief = store.get_belief(belief_id, include_inactive=True)
        if not belief:
            raise HTTPException(status_code=404, detail="Belief not found")
        return {"belief_id": belief_id, "items": store.get_belief_impact(belief_id, limit=limit)}

    @app.get("/api/debugger/contradictions")
    def get_contradictions(user_id: Optional[str] = None) -> Dict[str, Any]:
        store = get_store()
        selected_user = _resolve_user_id(store, user_id)
        pairs = []
        for left, right in store.get_contradictions(selected_user):
            shared_sources = len(
                set(left.source_memory_ids + left.source_episode_ids)
                & set(right.source_memory_ids + right.source_episode_ids)
            )
            pairs.append(
                {
                    "belief_a": _serialize_belief(left),
                    "belief_b": _serialize_belief(right),
                    "shared_source_overlap": shared_sources,
                    "recommended_resolution": _recommended_resolution(left, right),
                }
            )
        return {"user_id": selected_user, "items": pairs}

    @app.get("/api/debugger/activity")
    def get_activity(user_id: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        store = get_store()
        selected_user = _resolve_user_id(store, user_id)
        items = []
        for row in store.list_activity(user_id=selected_user, limit=limit):
            belief = store.get_belief(row["belief_id"], include_inactive=True)
            row["belief_claim"] = belief.claim if belief else ""
            row["belief_domain"] = belief.domain if belief else ""
            items.append(row)
        return {"user_id": selected_user, "items": items}

    @app.post("/api/debugger/beliefs/{belief_id}/mark-stale")
    def mark_belief_stale(belief_id: str, payload: ActionRequest) -> Dict[str, Any]:
        store = get_store()
        belief = store.mark_stale(belief_id, reason=payload.reason, actor=payload.actor)
        if not belief:
            raise HTTPException(status_code=404, detail="Belief not found")
        return _serialize_belief(belief)

    @app.post("/api/debugger/beliefs/{belief_id}/correct")
    def correct_belief(belief_id: str, payload: CorrectBeliefRequest) -> Dict[str, Any]:
        store = get_store()
        result = store.correct_belief(
            belief_id,
            new_claim=payload.new_claim,
            reason=payload.reason,
            actor=payload.actor,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Belief not found")
        old_belief, new_belief = result
        return {"old_belief": _serialize_belief(old_belief), "new_belief": _serialize_belief(new_belief)}

    @app.post("/api/debugger/beliefs/{belief_id}/tombstone")
    def tombstone_belief(belief_id: str, payload: ActionRequest) -> Dict[str, Any]:
        store = get_store()
        belief = store.tombstone_belief(belief_id, reason=payload.reason, actor=payload.actor)
        if not belief:
            raise HTTPException(status_code=404, detail="Belief not found")
        return _serialize_belief(belief)

    @app.post("/api/debugger/beliefs/{belief_id}/pin")
    def pin_belief(belief_id: str, payload: PinBeliefRequest) -> Dict[str, Any]:
        store = get_store()
        belief = store.pin_belief(
            belief_id,
            pinned=payload.pinned,
            reason=payload.reason,
            actor=payload.actor,
        )
        if not belief:
            raise HTTPException(status_code=404, detail="Belief not found")
        return _serialize_belief(belief)

    @app.post("/api/debugger/beliefs/merge")
    def merge_beliefs(payload: MergeBeliefsRequest) -> Dict[str, Any]:
        store = get_store()
        belief = store.merge_beliefs(
            survivor_id=payload.survivor_id,
            loser_id=payload.loser_id,
            reason=payload.reason,
            actor=payload.actor,
        )
        if not belief:
            raise HTTPException(status_code=404, detail="Beliefs not found")
        return _serialize_belief(belief)

    return app


def run() -> None:  # pragma: no cover - integration entry point
    if _FASTAPI_IMPORT_ERROR is not None:
        raise RuntimeError(
            "FastAPI and uvicorn are required for the Cognitive Debugger API. "
            "Install Dhee with the [api] extra."
        ) from _FASTAPI_IMPORT_ERROR

    import uvicorn

    app = create_app()
    host = os.environ.get("DHEE_DEBUGGER_HOST", "127.0.0.1")
    port = int(os.environ.get("DHEE_DEBUGGER_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
