"""Thread-native continuity state.

This is the cheap, per-thread bootstrap layer for Dhee. It should be read
before expensive cross-agent handoff recovery. When a live thread state is
available, Dhee can resume with a compact local snapshot instead of calling
`get_last_session()` on every prompt.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from dhee.core.kernel import get_last_session

_HANDOFF_AGENT_IDS = ("codex", "claude-code", "mcp-server", "dhee-cli")


def compact_thread_state(state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not state:
        return None
    return {
        "thread_id": state.get("thread_id"),
        "repo": state.get("repo"),
        "workspace_id": state.get("workspace_id"),
        "folder_path": state.get("folder_path"),
        "status": state.get("status"),
        "summary": state.get("summary"),
        "current_goal": state.get("current_goal"),
        "current_step": state.get("current_step"),
        "session_id": state.get("session_id"),
        "handoff_session_id": state.get("handoff_session_id"),
        "updated_at": state.get("updated_at"),
        "created_at": state.get("created_at"),
        "metadata": state.get("metadata") or {},
    }


def compact_session(session: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not session:
        return None
    return {
        "id": session.get("id"),
        "agent_id": session.get("agent_id"),
        "repo": session.get("repo"),
        "status": session.get("status"),
        "task_summary": session.get("task_summary"),
        "decisions": session.get("decisions") or session.get("decisions_made") or [],
        "files_touched": session.get("files_touched") or [],
        "todos": session.get("todos") or session.get("todos_remaining") or [],
        "updated": session.get("updated"),
        "source": session.get("source"),
    }


def resolve_continuity(
    db: Any,
    *,
    user_id: str = "default",
    repo: Optional[str] = None,
    thread_id: Optional[str] = None,
    fallback_log_recovery: bool = True,
    requester_agent_id: str = "codex",
) -> Dict[str, Any]:
    """Resolve continuity in the right order.

    1. Try thread-local continuity when a thread_id is present.
    2. If missing, fall back to cross-agent last-session recovery.
    """
    thread_state = None
    if thread_id:
        try:
            thread_state = db.get_thread_state(user_id=user_id, thread_id=thread_id)
        except Exception:
            thread_state = None

    compact_thread = compact_thread_state(thread_state)
    if compact_thread:
        return {
            "continuity_source": "thread_state",
            "thread_state": compact_thread,
            "last_session": None,
        }

    session = None
    for agent_id in _HANDOFF_AGENT_IDS:
        try:
            session = get_last_session(
                agent_id=agent_id,
                repo=repo,
                fallback_log_recovery=fallback_log_recovery,
                user_id=user_id,
                requester_agent_id=requester_agent_id,
            )
        except Exception:
            session = None
        if session:
            break

    compact_last = compact_session(session)
    return {
        "continuity_source": "last_session" if compact_last else "none",
        "thread_state": None,
        "last_session": compact_last,
    }
