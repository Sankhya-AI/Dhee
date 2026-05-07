"""Live shared-context delivery for active agents.

The workspace line is the durable shared stream. This module adds the
agent-facing contract on top of it: publish a broadcast, fetch unread
messages for a consumer, and mark those messages read so active sessions
do not get the same signal forever.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _abs(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return os.path.abspath(os.path.expanduser(raw))
    except Exception:
        return raw


def _path_anchor(*values: Optional[str]) -> Optional[str]:
    for value in values:
        path = _abs(value)
        if not path:
            continue
        if os.path.isdir(path):
            return path
        if os.path.isfile(path):
            return str(Path(path).parent)
    return None


def _consumer_id(
    *,
    consumer_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    harness: Optional[str] = None,
    runtime_id: Optional[str] = None,
    session_id: Optional[str] = None,
    native_session_id: Optional[str] = None,
) -> str:
    explicit = str(consumer_id or "").strip()
    if explicit:
        return explicit
    agent = str(agent_id or harness or runtime_id or "agent").strip() or "agent"
    session = str(session_id or native_session_id or "").strip()
    return f"{agent}:{session}" if session else agent


def ensure_workspace_for_path(
    db: Any,
    *,
    user_id: str = "default",
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
    workspace_id: Optional[str] = None,
    name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve or create a workspace anchored at an existing path.

    Existing workspace IDs win. Otherwise we match mounted/root paths. If
    nothing exists yet, create a path-scoped workspace so headless CLI
    agents can still share live context without opening the UI first.
    """
    if not hasattr(db, "upsert_workspace"):
        return None

    user_id = str(user_id or "default")
    explicit_ws = str(workspace_id or "").strip()
    if explicit_ws and hasattr(db, "get_workspace"):
        try:
            row = db.get_workspace(explicit_ws, user_id=user_id)
            if row:
                return row
        except Exception:
            pass

    anchor = _path_anchor(repo, cwd, source_path, workspace_id)
    if not anchor:
        return None

    if hasattr(db, "list_workspaces"):
        try:
            for ws in db.list_workspaces(user_id=user_id, limit=500):
                root = _abs(ws.get("root_path"))
                if root and _is_under(anchor, root):
                    return ws
        except Exception:
            pass

    label = str(name or Path(anchor).name or "Workspace").strip()
    return db.upsert_workspace(
        {
            "user_id": user_id,
            "name": label,
            "root_path": anchor,
            "metadata": {"source": "dhee_live_context", "auto_created": True},
        }
    )


def resolve_live_scope(
    db: Any,
    *,
    user_id: str = "default",
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
    workspace_id: Optional[str] = None,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    native_session_id: Optional[str] = None,
    runtime_id: Optional[str] = None,
    auto_create: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(workspace_id, project_id)`` for live context operations."""
    explicit_ws = str(workspace_id or "").strip()
    explicit_project = str(project_id or "").strip() or None
    if explicit_ws and hasattr(db, "get_workspace"):
        try:
            if db.get_workspace(explicit_ws, user_id=user_id):
                return explicit_ws, explicit_project
        except Exception:
            pass

    try:
        from dhee.core.workspace_line import resolve_workspace_and_project

        resolved_ws, resolved_project = resolve_workspace_and_project(
            db,
            user_id=user_id,
            session_id=session_id,
            native_session_id=native_session_id,
            runtime_id=runtime_id,
            repo=repo or workspace_id,
            cwd=cwd or repo or workspace_id,
            source_path=source_path,
        )
    except Exception:
        resolved_ws, resolved_project = None, None

    if resolved_ws:
        return resolved_ws, explicit_project or resolved_project

    if not auto_create:
        return None, explicit_project

    workspace = ensure_workspace_for_path(
        db,
        user_id=user_id,
        repo=repo or workspace_id,
        cwd=cwd,
        source_path=source_path,
    )
    return (workspace or {}).get("id"), explicit_project


def broadcast_live_context(
    db: Any,
    *,
    body: str,
    user_id: str = "default",
    title: Optional[str] = None,
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
    workspace_id: Optional[str] = None,
    project_id: Optional[str] = None,
    target_project_id: Optional[str] = None,
    channel: Optional[str] = None,
    message_kind: str = "broadcast",
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
    harness: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish a human/agent broadcast to the live workspace line."""
    text = str(body or "").strip()
    if not text:
        return {"error": "body is required"}

    ws_id, project = resolve_live_scope(
        db,
        user_id=user_id,
        repo=repo,
        cwd=cwd,
        source_path=source_path,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if not ws_id:
        return {"error": "workspace could not be resolved"}

    meta = dict(metadata or {})
    if agent_id:
        meta.setdefault("agent_id", agent_id)
    if harness:
        meta.setdefault("harness", harness)
        meta.setdefault("runtime_id", harness)
    if source_path:
        meta.setdefault("source_path", _abs(source_path))
    meta.setdefault("source", "dhee_live_context")

    row = db.add_workspace_line_message(
        {
            "workspace_id": ws_id,
            "project_id": project,
            "target_project_id": target_project_id,
            "user_id": user_id,
            "channel": channel or ("project" if project else "workspace"),
            "session_id": session_id,
            "task_id": task_id,
            "message_kind": message_kind,
            "title": title or "Live shared context",
            "body": text,
            "metadata": meta,
        }
    )
    if row:
        try:
            from dhee.core.workspace_line_bus import publish as _publish_bus

            _publish_bus(row)
        except Exception:
            pass
    return {"ok": bool(row), "message": row, "workspace_id": ws_id, "project_id": project}


def live_context_inbox(
    db: Any,
    *,
    user_id: str = "default",
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
    workspace_id: Optional[str] = None,
    project_id: Optional[str] = None,
    channel: Optional[str] = None,
    consumer_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    harness: Optional[str] = None,
    runtime_id: Optional[str] = None,
    session_id: Optional[str] = None,
    native_session_id: Optional[str] = None,
    limit: int = 10,
    mark_read: bool = True,
    include_own: bool = False,
) -> Dict[str, Any]:
    """Return unread live messages for an active agent consumer."""
    ws_id, project = resolve_live_scope(
        db,
        user_id=user_id,
        repo=repo,
        cwd=cwd,
        source_path=source_path,
        workspace_id=workspace_id,
        project_id=project_id,
        session_id=session_id,
        native_session_id=native_session_id,
        runtime_id=runtime_id,
    )
    cid = _consumer_id(
        consumer_id=consumer_id,
        agent_id=agent_id,
        harness=harness,
        runtime_id=runtime_id,
        session_id=session_id,
        native_session_id=native_session_id,
    )
    try:
        capped_limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        capped_limit = 10
    if not ws_id:
        return {
            "live": True,
            "status": "no_workspace",
            "workspace_id": None,
            "consumer_id": cid,
            "count": 0,
            "messages": [],
            "signal": "",
        }

    if not hasattr(db, "list_workspace_line_unread"):
        return {
            "live": False,
            "status": "unsupported",
            "workspace_id": ws_id,
            "consumer_id": cid,
            "count": 0,
            "messages": [],
            "signal": "",
        }

    rows = db.list_workspace_line_unread(
        workspace_id=ws_id,
        user_id=user_id,
        consumer_id=cid,
        project_id=project,
        channel=channel,
        limit=capped_limit,
    )
    aliases = _agent_aliases(cid, agent_id=agent_id, harness=harness, runtime_id=runtime_id)
    messages = [
        row
        for row in rows
        if include_own or not _looks_own_message(row, aliases=aliases, session_id=session_id or native_session_id)
    ][:capped_limit]

    if mark_read and messages and hasattr(db, "mark_workspace_line_messages_read"):
        db.mark_workspace_line_messages_read(
            workspace_id=ws_id,
            user_id=user_id,
            consumer_id=cid,
            message_ids=[str(row.get("id")) for row in messages if row.get("id")],
            metadata={"agent_id": agent_id, "harness": harness, "runtime_id": runtime_id},
        )

    signal = ""
    if messages:
        noun = "message" if len(messages) == 1 else "messages"
        signal = f"{len(messages)} unread Dhee live {noun}. Read before continuing."

    return {
        "live": True,
        "status": "ok",
        "workspace_id": ws_id,
        "project_id": project,
        "consumer_id": cid,
        "count": len(messages),
        "messages": messages,
        "signal": signal,
    }


def _is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _agent_aliases(
    consumer_id: str,
    *,
    agent_id: Optional[str] = None,
    harness: Optional[str] = None,
    runtime_id: Optional[str] = None,
) -> set[str]:
    aliases = {str(consumer_id or "").strip()}
    for value in (agent_id, harness, runtime_id):
        raw = str(value or "").strip()
        if raw:
            aliases.add(raw)
    return {alias for alias in aliases if alias}


def _looks_own_message(row: Dict[str, Any], *, aliases: Iterable[str], session_id: Optional[str]) -> bool:
    meta = row.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    values = {
        str(meta.get("agent_id") or "").strip(),
        str(meta.get("harness") or "").strip(),
        str(meta.get("runtime_id") or "").strip(),
        str(meta.get("native_session_id") or "").strip(),
    }
    if set(aliases) & {value for value in values if value}:
        return True
    session = str(session_id or "").strip()
    if session and str(row.get("session_id") or "").strip() == session:
        return True
    return False
