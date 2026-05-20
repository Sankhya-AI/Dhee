"""One-call context bootstrap for low-friction MCP clients.

The bootstrap is deliberately read-only: it composes existing continuity,
shared-task, result, and inbox views without marking inbox messages as read or
running any repo tools. Clients can approve/trust this once and avoid a stack of
separate startup prompts.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _bounded_int(args: Dict[str, Any], name: str, default: int, upper: int = 20) -> int:
    try:
        return max(1, min(upper, int(args.get(name, default))))
    except (TypeError, ValueError):
        return default


def _compact_shared_result(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "packet_kind": row.get("packet_kind"),
        "tool_name": row.get("tool_name"),
        "result_status": row.get("result_status"),
        "source_path": row.get("source_path"),
        "ptr": row.get("ptr"),
        "artifact_id": row.get("artifact_id"),
        "digest": row.get("digest"),
        "harness": row.get("harness"),
        "agent_id": row.get("agent_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "metadata": row.get("metadata") or {},
    }


def build_context_bootstrap(
    db: Any,
    args: Dict[str, Any],
    *,
    default_user_id: str = "default",
    default_agent_id: str = "agent",
) -> Dict[str, Any]:
    """Return the context-first startup packet in one local MCP call."""

    from dhee.core.handoff_snapshot import build_handoff_snapshot
    from dhee.core.live_context import live_context_inbox
    from dhee.core.shared_tasks import resolve_active_shared_task

    user_id = str(args.get("user_id") or default_user_id or "default")
    agent_id = str(args.get("agent_id") or default_agent_id or "agent")
    harness = str(args.get("harness") or os.environ.get("DHEE_HARNESS") or agent_id)
    repo: Optional[str] = None
    if args.get("repo"):
        repo = os.path.abspath(str(args.get("repo")))
    workspace_id = args.get("workspace_id") or repo
    errors: List[Dict[str, Any]] = []

    try:
        handoff = build_handoff_snapshot(
            db,
            user_id=user_id,
            repo=repo,
            workspace_id=workspace_id,
            thread_id=str(args.get("thread_id") or "").strip() or None,
            memory_limit=_bounded_int(args, "memory_limit", 5),
            artifact_limit=_bounded_int(args, "artifact_limit", 5),
            task_limit=_bounded_int(args, "task_limit", 5),
            intention_limit=_bounded_int(args, "intention_limit", 5),
        )
    except Exception as exc:  # noqa: BLE001 - bootstrap must degrade visibly.
        handoff = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        errors.append({"stage": "handoff", "error": handoff["error"]})

    shared_task: Dict[str, Any]
    shared_results: List[Dict[str, Any]] = []
    try:
        task = resolve_active_shared_task(
            db,
            user_id=user_id,
            shared_task_id=str(args.get("shared_task_id") or "").strip() or None,
            repo=repo,
            cwd=repo,
        )
        if task:
            shared_task = {
                "id": task.get("id"),
                "title": task.get("title"),
                "status": task.get("status"),
                "repo": task.get("repo"),
                "workspace_id": task.get("workspace_id"),
                "folder_path": task.get("folder_path"),
                "updated_at": task.get("updated_at"),
            }
            rows = db.list_shared_task_results(
                shared_task_id=str(task["id"]),
                limit=_bounded_int(args, "result_limit", 10, upper=100),
                result_status=args.get("result_status"),
                packet_kind=args.get("packet_kind"),
            )
            shared_results = [_compact_shared_result(row) for row in rows or []]
        else:
            shared_task = {"status": "not_found"}
    except Exception as exc:  # noqa: BLE001
        shared_task = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        errors.append({"stage": "shared_task", "error": shared_task["error"]})

    try:
        inbox = live_context_inbox(
            db,
            user_id=user_id,
            repo=repo,
            cwd=repo,
            workspace_id=workspace_id,
            project_id=args.get("project_id"),
            channel=args.get("channel"),
            consumer_id=args.get("consumer_id"),
            agent_id=agent_id,
            harness=harness,
            runtime_id=harness,
            session_id=args.get("session_id"),
            native_session_id=args.get("session_id"),
            limit=_bounded_int(args, "inbox_limit", 10, upper=50),
            mark_read=False,
            include_own=bool(args.get("include_own", False)),
        )
    except Exception as exc:  # noqa: BLE001
        inbox = {"status": "error", "messages": [], "error": f"{type(exc).__name__}: {exc}"}
        errors.append({"stage": "inbox", "error": inbox["error"]})

    return {
        "format": "dhee_context_bootstrap",
        "version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "harness": harness,
        "read_only": True,
        "replaces_startup_calls": [
            "dhee_handoff",
            "dhee_shared_task",
            "dhee_shared_task_results",
            "dhee_inbox",
        ],
        "handoff": handoff,
        "shared_task": shared_task,
        "shared_task_results": {
            "count": len(shared_results),
            "results": shared_results,
        },
        "inbox": inbox,
        "errors": errors,
    }
