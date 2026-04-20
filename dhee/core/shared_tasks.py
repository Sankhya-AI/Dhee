"""Ephemeral shared-task collaboration bus.

This layer sits between live routing and durable memory:

  * durable memory/artifacts keep what should survive
  * shared-task results keep task-local tool outputs visible across agents
  * thread-state keeps cheap per-thread continuity

The bus is intentionally ephemeral. When a shared task is closed, the raw
tool-result feed can be deleted while durable facts/artifacts/policies remain.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _abs_path(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return os.path.abspath(os.path.expanduser(raw))
    except Exception:
        return raw


def _path_candidates(
    *,
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
) -> list[str]:
    candidates: list[str] = []
    for value in (repo, cwd, source_path):
        if not value:
            continue
        normalized = _abs_path(value)
        if not normalized:
            continue
        if normalized not in candidates:
            candidates.append(normalized)
        try:
            parent = str(Path(normalized).parent)
        except Exception:
            parent = ""
        if parent and parent not in candidates:
            candidates.append(parent)
    return candidates


def resolve_active_shared_task(
    db: Any,
    *,
    user_id: str = "default",
    shared_task_id: Optional[str] = None,
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Pick the active shared task whose repo/workspace best matches the path."""
    if shared_task_id:
        return db.get_shared_task(shared_task_id, user_id=user_id)

    active_tasks = db.list_shared_tasks(
        user_id=user_id,
        status="active",
        repo=None,
        limit=50,
    )
    if not active_tasks:
        return None

    candidates = _path_candidates(repo=repo, cwd=cwd, source_path=source_path)
    if not candidates:
        return active_tasks[0]

    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for task in active_tasks:
        roots: list[str] = []
        for value in (task.get("repo"), task.get("workspace_id")):
            normalized = _abs_path(value)
            if normalized and normalized not in roots:
                roots.append(normalized)
        if not roots:
            continue
        for root in roots:
            for candidate in candidates:
                try:
                    common = os.path.commonpath([root, candidate])
                except ValueError:
                    continue
                if common != root:
                    continue
                score = len(root)
                if task.get("folder_path"):
                    folder = str(task.get("folder_path") or "").strip()
                    if folder not in {"", "."} and candidate.endswith(folder):
                        score += len(folder)
                if score > best_score:
                    best = task
                    best_score = score
    return best


def publish_shared_task_result(
    db: Any,
    *,
    user_id: str = "default",
    packet_kind: str,
    tool_name: str,
    digest: str,
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
    source_event_id: Optional[str] = None,
    ptr: Optional[str] = None,
    artifact_id: Optional[str] = None,
    shared_task_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    harness: Optional[str] = None,
    agent_id: Optional[str] = None,
    result_status: str = "completed",
) -> Optional[Dict[str, Any]]:
    """Publish a tool result into the active shared-task feed, if any."""
    task = resolve_active_shared_task(
        db,
        user_id=user_id,
        shared_task_id=shared_task_id,
        repo=repo,
        cwd=cwd,
        source_path=source_path,
    )
    if not task:
        return None

    result_key = shared_task_result_key(
        shared_task_id=str(task["id"]),
        packet_kind=packet_kind,
        source_event_id=source_event_id,
        source_path=_abs_path(source_path),
        ptr=ptr,
        artifact_id=artifact_id,
        digest=digest,
    )
    payload = {
        "shared_task_id": task["id"],
        "result_key": result_key,
        "user_id": user_id,
        "repo": task.get("repo") or _abs_path(repo or cwd),
        "workspace_id": task.get("workspace_id") or _abs_path(repo or cwd),
        "folder_path": task.get("folder_path"),
        "packet_kind": packet_kind,
        "tool_name": tool_name,
        "result_status": result_status,
        "source_event_id": source_event_id,
        "source_path": _abs_path(source_path),
        "ptr": ptr,
        "artifact_id": artifact_id,
        "digest": digest,
        "metadata": metadata or {},
        "session_id": session_id,
        "thread_id": thread_id,
        "harness": harness,
        "agent_id": agent_id,
    }
    result_id = db.save_shared_task_result(payload)
    return db.get_shared_task_result(result_id)


def publish_in_flight(
    db: Any,
    *,
    user_id: str = "default",
    packet_kind: str,
    tool_name: str,
    digest: str,
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
    source_event_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    harness: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return publish_shared_task_result(
        db,
        user_id=user_id,
        packet_kind=packet_kind,
        tool_name=tool_name,
        digest=digest,
        repo=repo,
        cwd=cwd,
        source_path=source_path,
        source_event_id=source_event_id,
        metadata=metadata,
        session_id=session_id,
        thread_id=thread_id,
        harness=harness,
        agent_id=agent_id,
        result_status="in_flight",
    )


def shared_task_snapshot(
    db: Any,
    *,
    user_id: str = "default",
    repo: Optional[str] = None,
    workspace_id: Optional[str] = None,
    source_path: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Compact active shared-task snapshot for handoff/bootstrap."""
    task = resolve_active_shared_task(
        db,
        user_id=user_id,
        repo=repo or workspace_id,
        cwd=workspace_id,
        source_path=source_path,
    )
    if not task:
        return {"task": None, "results": []}

    rows = db.list_shared_task_results(shared_task_id=task["id"], limit=limit)
    compact = []
    for row in rows:
        compact.append(
            {
                "id": row.get("id"),
                "packet_kind": row.get("packet_kind"),
                "tool_name": row.get("tool_name"),
                "source_path": row.get("source_path"),
                "artifact_id": row.get("artifact_id"),
                "ptr": row.get("ptr"),
                "digest": str(row.get("digest") or "")[:600],
                "harness": row.get("harness"),
                "agent_id": row.get("agent_id"),
                "created_at": row.get("created_at"),
                "metadata": row.get("metadata") or {},
            }
        )
    return {
        "task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "status": task.get("status"),
            "repo": task.get("repo"),
            "workspace_id": task.get("workspace_id"),
            "folder_path": task.get("folder_path"),
            "updated_at": task.get("updated_at"),
            "metadata": task.get("metadata") or {},
        },
        "results": compact,
    }


def shared_task_result_key(
    *,
    shared_task_id: str,
    packet_kind: str,
    source_event_id: Optional[str] = None,
    source_path: Optional[str] = None,
    ptr: Optional[str] = None,
    artifact_id: Optional[str] = None,
    digest: Optional[str] = None,
) -> str:
    seed = "|".join(
        [
            shared_task_id,
            packet_kind,
            str(source_event_id or ""),
            str(source_path or ""),
            str(ptr or ""),
            str(artifact_id or ""),
            hashlib.sha256(str(digest or "").encode("utf-8")).hexdigest()[:16],
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()
