"""Agent-activity emitter for the workspace information line.

This is the single choke point that publishes to the shared information
line whenever an agent tool-call produces durable output. Called from:

  * ``dhee.core.shared_tasks.publish_shared_task_result`` — covers router
    MCP tools (Read/Bash/Grep/Agent digests etc.)
  * ``dhee.hooks.claude_code`` PostToolUse — covers native Claude Code
    tools that don't go through the router
  * Direct MCP tool handlers that already call ``publish_shared_task_result``

Idempotent on ``(workspace_id, dedup_key)`` — retries and cross-source
double-emits (e.g. the same Read surfaced via both router and PostToolUse
hook) are silently dropped by the DB.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_TOOL_HEADLINES = {
    "Read": "read",
    "dhee_read": "read",
    "Bash": "bash",
    "dhee_bash": "bash",
    "Grep": "grep",
    "dhee_grep": "grep",
    "Glob": "glob",
    "Edit": "edit",
    "Write": "write",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Artifact": "asset",
    "WebFetch": "fetch",
    "WebSearch": "search",
}


def _abs(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return os.path.abspath(os.path.expanduser(raw))
    except Exception:
        return raw


def _short(value: Optional[str], n: int = 320) -> str:
    text = (value or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _headline(tool_name: Optional[str]) -> str:
    if not tool_name:
        return "tool"
    key = str(tool_name)
    return _TOOL_HEADLINES.get(key, key.lower())


def _auto_title(tool_name: Optional[str], source_path: Optional[str], digest: str) -> str:
    head = _headline(tool_name)
    if source_path:
        basename = os.path.basename(source_path) or source_path
        return f"{head} · {basename}"
    first_line = ""
    for line in (digest or "").splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    if first_line and len(first_line) <= 80:
        return f"{head} · {first_line}"
    return head


def _dedup_key(
    tool_name: Optional[str],
    packet_kind: Optional[str],
    source_event_id: Optional[str],
    source_path: Optional[str],
    ptr: Optional[str],
    artifact_id: Optional[str],
    session_id: Optional[str],
    digest: Optional[str],
) -> Optional[str]:
    parts = [
        str(tool_name or ""),
        str(packet_kind or ""),
        str(source_event_id or ""),
        str(source_path or ""),
        str(ptr or ""),
        str(artifact_id or ""),
        str(session_id or ""),
    ]
    meaningful = any(part for part in parts[1:])  # skip tool_name; needs >=1 discriminator
    if not meaningful:
        return None
    if digest and not (source_event_id or ptr or artifact_id):
        # Fall back to digest hash so pure one-shots still dedup.
        parts.append(hashlib.sha256(digest.encode("utf-8", errors="replace")).hexdigest()[:16])
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _list_workspaces(db: Any, user_id: str) -> List[Dict[str, Any]]:
    if not hasattr(db, "list_workspaces"):
        return []
    try:
        return list(db.list_workspaces(user_id=user_id, limit=200))
    except Exception:
        return []


def _list_workspace_mounts(db: Any, workspace_id: str, user_id: str) -> List[Dict[str, Any]]:
    if not hasattr(db, "list_workspace_mounts"):
        return []
    try:
        return list(db.list_workspace_mounts(workspace_id=workspace_id, user_id=user_id))
    except Exception:
        return []


def _path_is_under(path: str, root: str) -> bool:
    if not path or not root:
        return False
    if path == root:
        return True
    try:
        common = os.path.commonpath([path, root])
    except ValueError:
        return False
    return common == root


def _match_workspace_by_path(
    db: Any,
    *,
    user_id: str,
    candidates: List[str],
) -> Optional[str]:
    if not candidates:
        return None
    best_id: Optional[str] = None
    best_score = -1
    for ws in _list_workspaces(db, user_id):
        ws_id = str(ws.get("id") or "").strip()
        if not ws_id:
            continue
        roots: List[str] = []
        root_path = _abs(ws.get("root_path"))
        if root_path:
            roots.append(root_path)
        for mount in _list_workspace_mounts(db, ws_id, user_id):
            mount_path = _abs(mount.get("mount_path"))
            if mount_path and mount_path not in roots:
                roots.append(mount_path)
        for root in roots:
            for candidate in candidates:
                if not _path_is_under(candidate, root):
                    continue
                score = len(root)
                if score > best_score:
                    best_score = score
                    best_id = ws_id
    return best_id


def _project_workspace_for_project(db: Any, project_id: str, user_id: str) -> Optional[str]:
    if not project_id or not hasattr(db, "get_workspace_project"):
        return None
    try:
        project = db.get_workspace_project(project_id, user_id=user_id)
    except Exception:
        return None
    if not project:
        return None
    workspace_id = str(project.get("workspace_id") or "").strip()
    return workspace_id or None


def resolve_workspace_and_project(
    db: Any,
    *,
    user_id: str = "default",
    shared_task: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    native_session_id: Optional[str] = None,
    runtime_id: Optional[str] = None,
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve (workspace_id, project_id) UUIDs for an agent tool-call emission.

    Priority: agent_sessions lookup → shared_task.project_id → path match
    against workspace roots/mounts.
    """
    workspace_id: Optional[str] = None
    project_id: Optional[str] = None

    # 1. Authoritative: agent_sessions row seeded by the UI session mirror.
    if runtime_id and native_session_id and hasattr(db, "find_agent_session"):
        session = db.find_agent_session(
            runtime_id=runtime_id,
            native_session_id=native_session_id,
            user_id=user_id,
        )
        if session:
            workspace_id = str(session.get("workspace_id") or "").strip() or None
            project_id = str(session.get("project_id") or "").strip() or None
            if workspace_id:
                return workspace_id, project_id

    # 2. Shared task carries a project UUID; look up its workspace.
    if shared_task:
        shared_project_id = str(shared_task.get("project_id") or "").strip() or None
        if shared_project_id:
            project_id = project_id or shared_project_id
            ws = _project_workspace_for_project(db, shared_project_id, user_id)
            if ws:
                return ws, shared_project_id

    # 3. Path-based match against workspace roots + mounts.
    candidates: List[str] = []
    for value in (source_path, cwd, repo):
        abs_value = _abs(value)
        if abs_value and abs_value not in candidates:
            candidates.append(abs_value)
    ws = _match_workspace_by_path(db, user_id=user_id, candidates=candidates)
    return ws, project_id


def emit_agent_activity(
    db: Any,
    *,
    user_id: str = "default",
    tool_name: str,
    packet_kind: str,
    digest: str,
    shared_task: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    native_session_id: Optional[str] = None,
    runtime_id: Optional[str] = None,
    repo: Optional[str] = None,
    cwd: Optional[str] = None,
    source_path: Optional[str] = None,
    source_event_id: Optional[str] = None,
    ptr: Optional[str] = None,
    artifact_id: Optional[str] = None,
    task_id: Optional[str] = None,
    harness: Optional[str] = None,
    agent_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    result_status: str = "completed",
) -> Optional[Dict[str, Any]]:
    """Publish an agent tool-call onto the workspace information line.

    Returns the created row, or None if:
      * no workspace could be resolved (silently skipped; the line is
        workspace-scoped)
      * the dedup key matched an existing entry (silently skipped)
      * the DB doesn't expose ``add_workspace_line_message`` (old schema)
    """
    if not hasattr(db, "add_workspace_line_message"):
        return None

    workspace_id, project_id = resolve_workspace_and_project(
        db,
        user_id=user_id,
        shared_task=shared_task,
        session_id=session_id,
        native_session_id=native_session_id,
        runtime_id=runtime_id,
        repo=repo,
        cwd=cwd,
        source_path=source_path,
    )
    if not workspace_id:
        return None

    dedup = _dedup_key(
        tool_name=tool_name,
        packet_kind=packet_kind,
        source_event_id=source_event_id,
        source_path=_abs(source_path),
        ptr=ptr,
        artifact_id=artifact_id,
        session_id=session_id or native_session_id,
        digest=digest,
    )

    title = _auto_title(tool_name, source_path, digest)
    body = _short(digest)

    meta: Dict[str, Any] = {
        "tool_name": tool_name,
        "packet_kind": packet_kind,
        "harness": harness,
        "agent_id": agent_id,
        "source_path": _abs(source_path),
        "ptr": ptr,
        "artifact_id": artifact_id,
        "native_session_id": native_session_id,
        "runtime_id": runtime_id,
        "source_event_id": source_event_id,
        "result_status": result_status,
    }
    if metadata:
        for key, value in metadata.items():
            meta.setdefault(key, value)

    # Auto-link a project/workspace asset if this tool call touched one.
    # Agents reading/grepping/editing an uploaded file will now show up in
    # the asset drawer as "processed by <agent> <when>".
    if source_path and "asset_id" not in meta and hasattr(db, "find_project_asset_by_storage_path"):
        try:
            abs_source = _abs(source_path)
            if abs_source:
                asset = db.find_project_asset_by_storage_path(abs_source, user_id=user_id)
                if asset and asset.get("id"):
                    meta["asset_id"] = str(asset["id"])
                    if not meta.get("project_id"):
                        meta["project_id"] = asset.get("project_id")
        except Exception:
            pass

    kind = packet_kind if packet_kind.startswith(("agent.", "tool.")) else f"tool.{packet_kind}"
    channel = "project" if project_id else "workspace"

    row = db.add_workspace_line_message(
        {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "user_id": user_id,
            "channel": channel,
            "session_id": session_id or native_session_id,
            "task_id": task_id,
            "message_kind": kind,
            "title": title,
            "body": body,
            "metadata": meta,
            "dedup_key": dedup,
        }
    )

    # Fan out through the in-process bus so SSE subscribers see the
    # message in this same event-loop tick. Only publish when the DB
    # actually created a row (dedup collisions return None).
    if row:
        try:
            from dhee.core.workspace_line_bus import publish as _publish_bus

            _publish_bus(row)
        except Exception:
            pass

    return row
