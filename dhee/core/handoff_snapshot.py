"""Derived structured handoff snapshot for portable resume context."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dhee.configs.base import _dhee_data_dir
from dhee.core.intention import IntentionStore
from dhee.core.shared_tasks import shared_task_snapshot
from dhee.core.task_state import TaskStateStore
from dhee.core.thread_state import resolve_continuity

_NOISE_KINDS = {"artifact_chunk", "doc_chunk"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_dir(name: str) -> str:
    return os.path.join(_dhee_data_dir(), name)


def _recent_memories(db: Any, *, user_id: str, limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with db._get_connection() as conn:
        raw_rows = conn.execute(
            """
            SELECT *
            FROM memories
            WHERE user_id = ? AND tombstone = 0
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, max(limit * 4, 20)),
        ).fetchall()
        for row in raw_rows:
            parsed = db._row_to_dict(row)
            metadata = dict(parsed.get("metadata") or {})
            if metadata.get("kind") in _NOISE_KINDS:
                continue
            rows.append(
                {
                    "id": parsed.get("id"),
                    "memory": str(parsed.get("memory", ""))[:240],
                    "updated_at": parsed.get("updated_at"),
                    "source_type": parsed.get("source_type") or metadata.get("source_type"),
                    "source_app": parsed.get("source_app") or metadata.get("source_app"),
                    "memory_type": parsed.get("memory_type"),
                    "metadata_kind": metadata.get("kind"),
                }
            )
            if len(rows) >= limit:
                break
    return rows


def _recent_artifacts(db: Any, *, user_id: str, workspace_id: Optional[str], limit: int) -> List[Dict[str, Any]]:
    rows = db.list_artifacts(
        user_id=user_id,
        workspace_id=workspace_id,
        limit=limit,
    )
    results: List[Dict[str, Any]] = []
    for row in rows:
        artifact = db.get_artifact(str(row.get("artifact_id") or ""))
        bindings = (artifact or {}).get("bindings", []) if artifact else []
        primary = bindings[0] if bindings else {}
        results.append(
            {
                "artifact_id": row.get("artifact_id"),
                "filename": row.get("filename"),
                "lifecycle_state": row.get("lifecycle_state"),
                "extraction_count": row.get("extraction_count", 0),
                "last_extraction_at": row.get("last_extraction_at"),
                "workspace_id": primary.get("workspace_id"),
                "folder_path": primary.get("folder_path"),
                "source_path": primary.get("source_path"),
            }
        )
    return results


def _active_intentions(*, user_id: str, limit: int) -> List[Dict[str, Any]]:
    store = IntentionStore(data_dir=_store_dir("intentions"))
    return [item.to_dict() for item in store.get_active(user_id)[:limit]]


def _task_snapshot(*, user_id: str, limit: int) -> Dict[str, Any]:
    store = TaskStateStore(data_dir=_store_dir("tasks"))
    active = store.get_active_task(user_id)
    recent = [task.to_compact() for task in store.get_recent_tasks(user_id, limit=limit)]
    return {
        "active": active.to_compact() if active else None,
        "recent": recent,
    }


def build_handoff_snapshot(
    db: Any,
    *,
    user_id: str = "default",
    repo: Optional[str] = None,
    workspace_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    memory_limit: int = 5,
    artifact_limit: int = 5,
    task_limit: int = 5,
    intention_limit: int = 5,
    shared_result_limit: int = 5,
) -> Dict[str, Any]:
    repo = os.path.abspath(repo) if repo else None
    workspace_id = workspace_id or repo
    continuity = resolve_continuity(
        db,
        user_id=user_id,
        repo=repo,
        thread_id=thread_id,
        fallback_log_recovery=True,
        requester_agent_id="codex",
    )

    tasks = _task_snapshot(user_id=user_id, limit=task_limit)
    intentions = _active_intentions(user_id=user_id, limit=intention_limit)
    memories = _recent_memories(db, user_id=user_id, limit=memory_limit)
    artifacts = _recent_artifacts(
        db,
        user_id=user_id,
        workspace_id=workspace_id,
        limit=artifact_limit,
    )
    shared = shared_task_snapshot(
        db,
        user_id=user_id,
        repo=repo,
        workspace_id=workspace_id,
        limit=shared_result_limit,
    )

    resume_hints: List[str] = []
    compact_thread = continuity.get("thread_state")
    compact_session = continuity.get("last_session")
    if compact_thread:
        if compact_thread.get("summary"):
            resume_hints.append(f"thread: {compact_thread['summary']}")
        if compact_thread.get("current_goal"):
            resume_hints.append(f"goal: {compact_thread['current_goal']}")
        if compact_thread.get("current_step"):
            resume_hints.append(f"next step: {compact_thread['current_step']}")
    elif compact_session:
        for todo in compact_session.get("todos", [])[:3]:
            resume_hints.append(f"todo: {todo}")
    active_task = tasks.get("active")
    if active_task:
        if active_task.get("goal"):
            resume_hints.append(f"active task: {active_task['goal']}")
        if active_task.get("current_step"):
            resume_hints.append(f"next step: {active_task['current_step']}")
    for intention in intentions[:2]:
        resume_hints.append(f"intention: {intention.get('action_payload') or intention.get('description')}")
    shared_task = shared.get("task")
    if shared_task:
        resume_hints.append(f"shared task: {shared_task.get('title')}")
        for row in (shared.get("results") or [])[:2]:
            tool_name = row.get("tool_name") or row.get("packet_kind")
            digest = str(row.get("digest") or "").strip().splitlines()
            if digest:
                resume_hints.append(f"{tool_name}: {digest[0][:120]}")

    return {
        "format": "dhee_handoff",
        "version": "1",
        "generated_at": _utcnow(),
        "user_id": user_id,
        "repo": repo,
        "workspace_id": workspace_id,
        "thread_id": thread_id,
        "continuity_source": continuity.get("continuity_source", "none"),
        "thread_state": compact_thread,
        "last_session": compact_session,
        "tasks": tasks,
        "intentions": intentions,
        "shared_task": shared_task,
        "shared_task_results": shared.get("results") or [],
        "recent_memories": memories,
        "recent_artifacts": artifacts,
        "resume_hints": resume_hints[:8],
    }
