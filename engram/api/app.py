"""Engram core REST API — lightweight handoff endpoints (no auth required).

These endpoints mirror the enterprise ``/v1/handoff/*`` routes but delegate
directly to ``engram.core.kernel`` without session/token enforcement.
They are intended for local development and for the ``prompt_context.py`` hook
which fires as a subprocess with no auth context.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Engram Core API",
    version="0.1.0",
    description="Lightweight handoff + memory endpoints.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class CheckpointRequest(BaseModel):
    task_summary: Optional[str] = None
    event_type: str = "hook_checkpoint"
    agent_id: str = "claude-code"
    context_snapshot: Optional[str] = None
    repo_path: Optional[str] = None
    status: Optional[str] = None
    decisions_made: Optional[List[str]] = None
    files_touched: Optional[List[str]] = None
    todos_remaining: Optional[List[str]] = None
    blockers: Optional[List[str]] = None
    key_commands: Optional[List[str]] = None
    test_results: Optional[str] = None


class RecoverRequest(BaseModel):
    repo_path: str
    agent_id: str = "claude-code"


class SessionDigestRequest(BaseModel):
    task_summary: str
    repo: Optional[str] = None
    status: str = "active"
    agent_id: str = "claude-code"
    decisions_made: Optional[List[str]] = None
    files_touched: Optional[List[str]] = None
    todos_remaining: Optional[List[str]] = None
    blockers: Optional[List[str]] = None
    key_commands: Optional[List[str]] = None
    test_results: Optional[str] = None


# ---------------------------------------------------------------------------
# Handoff endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/handoff/checkpoint")
async def handoff_checkpoint(request: CheckpointRequest):
    """Receive a lightweight checkpoint from the hook or an agent.

    Creates an engram-bus session (if needed) and writes a checkpoint snapshot.
    """
    from engram.core.kernel import _get_bus

    bus = None
    try:
        bus = _get_bus()

        # Find or create a session for this agent
        session = bus.get_session(agent_id=request.agent_id)
        if session is None:
            sid = bus.save_session(
                agent_id=request.agent_id,
                repo=request.repo_path,
                status=request.status or "active",
                task_summary=request.task_summary or "",
            )
        else:
            sid = session["id"]
            # Update task_summary if provided
            updates: Dict[str, Any] = {}
            if request.task_summary:
                updates["task_summary"] = request.task_summary
            if request.status:
                updates["status"] = request.status
            if updates:
                bus.update_session(sid, **updates)

        snapshot = {
            "event_type": request.event_type,
            "task_summary": request.task_summary,
            "context_snapshot": request.context_snapshot,
            "files_touched": request.files_touched or [],
            "key_commands": request.key_commands or [],
            "decisions_made": request.decisions_made or [],
            "todos_remaining": request.todos_remaining or [],
            "blockers": request.blockers or [],
            "test_results": request.test_results,
        }
        cid = bus.checkpoint(sid, request.agent_id, snapshot)

        return {"status": "ok", "session_id": sid, "checkpoint_id": cid}

    except Exception as exc:
        logger.exception("Checkpoint failed")
        return {"status": "error", "detail": str(exc)}
    finally:
        if bus is not None:
            try:
                bus.close()
            except Exception:
                pass


@app.get("/v1/handoff/sessions/last")
async def handoff_last_session(
    agent_id: Optional[str] = Query(default=None),
    repo: Optional[str] = Query(default=None),
    fallback_log_recovery: bool = Query(default=True),
):
    """Get the last session, falling back to JSONL log parsing."""
    from engram.core.kernel import get_last_session

    session = get_last_session(
        agent_id=agent_id or "mcp-server",
        repo=repo,
        fallback_log_recovery=fallback_log_recovery,
    )
    if session is None:
        return {"status": "no_session", "message": "No previous session found."}
    return session


@app.post("/v1/handoff/recover")
async def handoff_recover(request: RecoverRequest):
    """Direct log recovery — parse JSONL logs without checking bus first."""
    from engram.core.log_parser import find_latest_log, parse_conversation_log

    log_path = find_latest_log(request.repo_path)
    if log_path is None:
        return {"status": "no_logs", "message": "No conversation logs found."}

    digest = parse_conversation_log(log_path)
    if digest.get("message_count", 0) == 0:
        return {"status": "empty_log", "message": "Log file was empty."}

    return digest


@app.post("/v1/handoff/sessions/digest")
async def save_handoff_digest(request: SessionDigestRequest):
    """Save a session digest (lightweight, no auth)."""
    from engram.core.kernel import save_session_digest

    result = save_session_digest(
        task_summary=request.task_summary,
        agent_id=request.agent_id,
        repo=request.repo,
        status=request.status,
        decisions_made=request.decisions_made,
        files_touched=request.files_touched,
        todos_remaining=request.todos_remaining,
        blockers=request.blockers,
        key_commands=request.key_commands,
        test_results=request.test_results,
    )
    return result
