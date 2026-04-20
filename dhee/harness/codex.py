"""Codex harness adapter.

Codex does not expose Claude-style hook callbacks, but it *does* persist a
structured JSONL event stream. Dhee treats that stream as the native Codex
integration surface:

* MCP config + Codex global instructions make Dhee the primary memory/router
* incremental stream ingestion captures post-tool results and attachments
* session-end dispatch is still supported as a catch-up/teardown path

This keeps the adapter honest: Codex is native, but its fidelity is
``stream`` rather than hook-callback ``live``.
"""

from __future__ import annotations

from typing import Any, Dict

from dhee.harness.base import CanonicalEvent, HarnessAdapter


_CODEX_EVENT_MAP: Dict[str, CanonicalEvent] = {
    "session_end": CanonicalEvent.SESSION_END,
    "SessionEnd": CanonicalEvent.SESSION_END,
}


class CodexAdapter(HarnessAdapter):
    name = "codex"
    event_map = _CODEX_EVENT_MAP
    fidelity = "stream"

    def __init__(self) -> None:
        super().__init__()
        self.register(CanonicalEvent.SESSION_END, self._on_session_end)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_session_end(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Ingest the latest Codex transcript on a session-end signal.

        Payload may provide an explicit ``jsonl_path`` to ingest a
        specific transcript; otherwise the most recent Codex session
        log is used.
        """
        try:
            from dhee import Dhee
            from dhee.core.artifacts import ArtifactManager
            from dhee.core.codex_stream import find_latest_codex_log, sync_latest_codex_stream

            jsonl_path = payload.get("jsonl_path") or find_latest_codex_log(
                payload.get("sessions_root")
            )
            if not jsonl_path:
                return {"status": "no_log"}

            dhee = Dhee(
                user_id=payload.get("user_id", "default"),
                auto_context=False,
                auto_checkpoint=False,
            )
            manager = ArtifactManager(dhee._engram.memory.db, engram=dhee._engram)
            stats = sync_latest_codex_stream(
                manager,
                dhee._engram.memory.db,
                user_id=payload.get("user_id", "default"),
                sessions_root=payload.get("sessions_root"),
                log_path=jsonl_path,
            )
            return stats
        except Exception as exc:
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
