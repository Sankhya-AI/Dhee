from __future__ import annotations

import base64
from pathlib import Path

from dhee.world_memory.capture_store import CaptureStore
from dhee.world_memory.service import MemoryOSService
from dhee.world_memory.session_graph import SessionGraphStore
from dhee.world_memory.store import WorldMemoryStore


class FakeMemoryClient:
    def __init__(self) -> None:
        self.rows = []

    def remember(self, content: str, **kwargs):
        row = {
            "id": f"mem-{len(self.rows) + 1}",
            "memory": content,
            "content": content,
            "metadata": dict(kwargs.get("metadata") or {}),
            "source_app": kwargs.get("source_app"),
            "created_at": "2026-04-23T00:00:00+00:00",
        }
        self.rows.append(row)
        return row

    def recall(self, query: str, **kwargs):
        needle = query.lower()
        return [
            row for row in self.rows if needle in str(row.get("memory") or "").lower()
        ][: kwargs.get("limit", 5)]

    def recent(self, **kwargs):
        return list(reversed(self.rows))[: kwargs.get("limit", 12)]


def _service(tmp_path: Path) -> tuple[MemoryOSService, FakeMemoryClient]:
    memory = FakeMemoryClient()
    service = MemoryOSService(
        capture_store=CaptureStore(str(tmp_path / "capture.db")),
        world_store=WorldMemoryStore(str(tmp_path / "world.db")),
        graph_store=SessionGraphStore(str(tmp_path / "sessions")),
        memory_client=memory,
    )
    return service, memory


def test_pointer_capture_session_graph_and_distillation(tmp_path: Path) -> None:
    service, memory = _service(tmp_path)
    started = service.start_capture_session(user_id="default", source_app="chrome")
    session_id = started["session"]["id"]

    action = service.record_action(
        {
            "session_id": session_id,
            "action_type": "click",
            "capture_mode": "dom",
            "title": "Interesting Docs",
            "url": "https://example.com/docs",
            "surface_type": "web_page",
            "path_hint": ["docs"],
            "target": {"role": "link", "text": "Setup"},
            "before_context": "<html><body>Docs home</body></html>",
            "after_context": "<html><body>Setup steps npm install</body></html>",
        }
    )
    observation = service.record_observation(
        {
            "session_id": session_id,
            "action_id": action["action"]["id"],
            "title": "Interesting Docs",
            "url": "https://example.com/docs",
            "surface_type": "web_page",
            "text": "Installation section explains how to bootstrap the project and run npm install before starting the local server.",
            "structured": {"headings": ["Installation"], "persist_hint": True},
            "kind": "selection",
            "source_kind": "dom",
            "confidence": 0.99,
        }
    )
    image = base64.b64encode(b"fake-png-image").decode("ascii")
    artifact = service.record_artifact(
        {
            "session_id": session_id,
            "action_id": action["action"]["id"],
            "title": "Interesting Docs",
            "url": "https://example.com/docs",
            "surface_type": "web_page",
            "content_base64": image,
            "mime_type": "image/png",
        }
    )
    ended = service.end_capture_session(session_id, distill=True)

    session_path = Path(started["sessionPath"])
    assert (session_path / "session_manifest.json").exists()
    assert (session_path / "actions.jsonl").exists()
    assert (session_path / "observations.jsonl").exists()
    assert action["worldTransition"]["ptr"].startswith("wm-")
    assert observation["memory"]["metadata"]["memory_type"] == "surface_memory_card"
    assert artifact["artifact"]["mime_type"] == "image/png"
    assert ended["summaryMemory"]["metadata"]["memory_type"] == "capture_session_summary"
    assert ended["graph"]["manifest"]["artifact_count"] == 1
    assert any(row["metadata"].get("memory_type") == "surface_memory_card" for row in memory.rows)


def test_surface_revisit_merges_same_surface(tmp_path: Path) -> None:
    service, _memory = _service(tmp_path)
    started = service.start_capture_session(user_id="default", source_app="chrome")
    session_id = started["session"]["id"]

    for label in ["README", "README updated"]:
        service.record_action(
            {
                "session_id": session_id,
                "action_type": "click",
                "title": "Repo README",
                "url": "https://example.com/repo#readme",
                "surface_type": "web_page_section",
                "path_hint": ["repo", "README"],
                "target": {"role": "heading", "text": label},
            }
        )

    loaded = service.get_capture_session(session_id)
    assert len(loaded["graph"]["surfaces"]) == 1
    assert len(loaded["graph"]["actions"]) == 2
    assert loaded["graph"]["manifest"]["page_count"] == 1


def test_artifact_dedup_and_cleanup(tmp_path: Path) -> None:
    service, _memory = _service(tmp_path)
    started = service.start_capture_session(user_id="default", source_app="chrome")
    session_id = started["session"]["id"]
    shared_payload = {
        "session_id": session_id,
        "title": "Design",
        "url": "https://example.com/design",
        "surface_type": "web_page",
        "content_base64": base64.b64encode(b"same-image").decode("ascii"),
        "mime_type": "image/png",
    }

    first = service.record_artifact(shared_payload)
    second = service.record_artifact(shared_payload)
    assert first["deduped"] is False
    assert second["deduped"] is True

    expired = service.record_artifact(
        {
            **shared_payload,
            "content_base64": base64.b64encode(b"old-image").decode("ascii"),
            "created_at": "2026-04-20T00:00:00+00:00",
            "ttl_hours": 1,
        }
    )
    expired_path = Path(expired["artifact"]["path"])
    assert expired_path.exists()

    cleanup = service.cleanup_expired_artifacts()
    assert cleanup["removed"] >= 1
    assert not expired_path.exists()
