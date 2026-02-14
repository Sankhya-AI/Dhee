"""Handoff kernel — combines engram-bus sessions with JSONL log fallback.

Provides ``get_last_session()`` which first checks the engram-bus SQLite store
for an existing handoff session, and if none is found, falls back to parsing
Claude Code's ``.jsonl`` conversation logs to reconstruct context.
"""

import logging
import os
from typing import Dict, List, Optional

from engram.core.log_parser import find_latest_log, parse_conversation_log

logger = logging.getLogger(__name__)

# Default path for the handoff SQLite database
_DEFAULT_DB = os.path.join(os.path.expanduser("~"), ".engram", "handoff.db")


def _get_bus(db_path: Optional[str] = None):
    """Lazy-import and create a Bus instance with a handoff store."""
    from engram_bus.bus import Bus
    return Bus(db_path=db_path or os.environ.get("ENGRAM_HANDOFF_DB", _DEFAULT_DB))


def get_last_session(
    agent_id: str = "mcp-server",
    repo: Optional[str] = None,
    fallback_log_recovery: bool = True,
    db_path: Optional[str] = None,
) -> Optional[Dict]:
    """Get the last session for *agent_id*, falling back to JSONL logs.

    1. Try ``bus.get_session(agent_id=agent_id)``
    2. If found, attach latest checkpoint journal entries.
    3. If not found **and** *fallback_log_recovery* is ``True`` **and** *repo*
       is provided, parse the most recent Claude Code conversation log for
       that repo and return a reconstructed digest.

    Parameters
    ----------
    agent_id:
        The source agent whose session to load (default ``"mcp-server"``).
    repo:
        Absolute path to the repository root, used for log-based fallback.
    fallback_log_recovery:
        Whether to fall back to JSONL log parsing if no bus session exists.
    db_path:
        Override path for the handoff SQLite database.

    Returns
    -------
    dict or None
    """
    bus = None
    try:
        bus = _get_bus(db_path)
        session = bus.get_session(agent_id=agent_id)

        if session is not None:
            # Attach latest checkpoints
            try:
                checkpoints = bus.list_checkpoints(session_id=session["id"])
                session["checkpoints"] = checkpoints[:5]  # most recent 5
            except Exception:
                session["checkpoints"] = []
            session["source"] = "bus_session"
            return session

    except Exception:
        logger.debug("Bus session lookup failed", exc_info=True)
    finally:
        if bus is not None:
            try:
                bus.close()
            except Exception:
                pass

    # --- Fallback: parse conversation logs ---
    if not fallback_log_recovery or not repo:
        return None

    try:
        log_path = find_latest_log(repo)
        if log_path is None:
            return None
        digest = parse_conversation_log(log_path)
        if digest.get("message_count", 0) == 0:
            return None
        return digest
    except Exception:
        logger.debug("Log fallback failed", exc_info=True)
        return None


def save_session_digest(
    task_summary: str,
    agent_id: str = "claude-code",
    repo: Optional[str] = None,
    status: str = "active",
    decisions_made: Optional[List[str]] = None,
    files_touched: Optional[List[str]] = None,
    todos_remaining: Optional[List[str]] = None,
    blockers: Optional[List[str]] = None,
    key_commands: Optional[List[str]] = None,
    test_results: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict:
    """Save a session digest to the engram-bus handoff store.

    Returns ``{"status": "saved", "session_id": "<id>"}``.
    """
    bus = None
    try:
        bus = _get_bus(db_path)

        metadata = {}
        if blockers:
            metadata["blockers"] = blockers
        if key_commands:
            metadata["key_commands"] = key_commands
        if test_results:
            metadata["test_results"] = test_results

        sid = bus.save_session(
            agent_id=agent_id,
            repo=repo,
            status=status,
            task_summary=task_summary,
            decisions=decisions_made or [],
            files_touched=files_touched or [],
            todos=todos_remaining or [],
            metadata=metadata,
        )
        return {"status": "saved", "session_id": sid}
    finally:
        if bus is not None:
            try:
                bus.close()
            except Exception:
                pass
