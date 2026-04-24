from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from dhee.ui.server import create_app


class FakeRawMemory:
    def __init__(self) -> None:
        self.rows = []

    def add(self, messages, **kwargs):
        content = messages if isinstance(messages, str) else str(messages)
        row = {
            "id": f"mem-{len(self.rows) + 1}",
            "memory": content,
            "content": content,
            "metadata": dict(kwargs.get("metadata") or {}),
            "source_app": kwargs.get("source_app"),
            "created_at": "2026-04-23T00:00:00+00:00",
        }
        self.rows.append(row)
        return {"results": [row]}

    def search(self, query, **kwargs):
        needle = str(query or "").lower()
        matches = [
            row for row in self.rows if needle in str(row.get("memory") or "").lower()
        ]
        return {"results": matches[: kwargs.get("limit", 5)]}

    def get_all(self, **kwargs):
        return {"results": list(reversed(self.rows))[: kwargs.get("limit", 20)]}


class FakeConflictMemory(FakeRawMemory):
    def __init__(self) -> None:
        super().__init__()
        self.resolved: list[tuple[str, str]] = []

    def get_conflicts(self):
        return [
            {
                "id": "conflict-1",
                "severity": "medium",
                "reason": "Two memories disagree about the preferred editor",
                "belief_a": {
                    "id": "a",
                    "content": "User prefers Vim",
                    "confidence": 0.61,
                    "created": "2026-04-23T00:00:00+00:00",
                    "source": "session-a",
                    "tier": "medium",
                },
                "belief_b": {
                    "id": "b",
                    "content": "User prefers VS Code",
                    "confidence": 0.72,
                    "created": "2026-04-23T00:05:00+00:00",
                    "source": "session-b",
                    "tier": "medium",
                },
            }
        ]


class FakeResolvableConflictMemory(FakeConflictMemory):
    def resolve_conflict(self, conflict_id, action, merged_content=None, reason=None):
        self.resolved.append((conflict_id, action, merged_content, reason))
        return {
            "conflict_id": conflict_id,
            "action": action,
            "merged_content": merged_content,
            "reason": reason,
        }


def test_capture_memory_os_endpoints(monkeypatch, tmp_path) -> None:
    import dhee.mcp_server as mcp_server

    fake_memory = FakeRawMemory()
    monkeypatch.setattr(mcp_server, "get_memory_instance", lambda: fake_memory)
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    app = create_app(serve_static=False)
    client = TestClient(app)

    started = client.post("/api/capture/session/start", json={"source_app": "chrome"})
    assert started.status_code == 200
    session_id = started.json()["session"]["id"]

    action = client.post(
        "/api/capture/action",
        json={
            "session_id": session_id,
            "action_type": "click",
            "title": "Docs",
            "url": "https://example.com/docs",
            "surface_type": "web_page",
            "target": {"role": "link", "text": "Install"},
            "before_context": "<html><body>Docs</body></html>",
            "after_context": "<html><body>Install section</body></html>",
        },
    )
    assert action.status_code == 200

    observation = client.post(
        "/api/capture/observation",
        json={
            "session_id": session_id,
            "title": "Docs",
            "url": "https://example.com/docs",
            "surface_type": "web_page",
            "text": "Install section with a detailed setup guide and command examples for the user.",
            "structured": {"persist_hint": True, "headings": ["Install"]},
        },
    )
    assert observation.status_code == 200

    artifact = client.post(
        "/api/capture/artifact",
        json={
            "session_id": session_id,
            "title": "Docs",
            "url": "https://example.com/docs",
            "surface_type": "web_page",
            "content_base64": base64.b64encode(b"png-bits").decode("ascii"),
            "mime_type": "image/png",
        },
    )
    assert artifact.status_code == 200

    now = client.get("/api/memory/now")
    assert now.status_code == 200
    assert now.json()["live"] is True
    assert len(now.json()["activeCapture"]) == 1

    ended = client.post("/api/capture/session/end", json={"session_id": session_id})
    assert ended.status_code == 200
    assert ended.json()["summaryMemory"]["metadata"]["memory_type"] == "capture_session_summary"

    ask = client.post("/api/memory/ask", json={"query": "install guide"})
    assert ask.status_code == 200
    assert "worldMemory" in ask.json()

    prefs = client.get("/api/capture/preferences")
    assert prefs.status_code == 200
    assert any(item["source_app"] == "chrome" for item in prefs.json()["items"])


def test_conflicts_are_truthful_when_resolution_is_unavailable(monkeypatch, tmp_path) -> None:
    import dhee.mcp_server as mcp_server

    monkeypatch.setattr(mcp_server, "get_memory_instance", lambda: FakeConflictMemory())
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    app = create_app(serve_static=False)
    client = TestClient(app)

    snapshot = client.get("/api/conflicts")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["live"] is True
    assert body["supported"] is False
    assert body["resolutionMode"] == "read-only"
    assert len(body["conflicts"]) == 1

    resolution = client.post("/api/conflicts/conflict-1/resolve", json={"action": "MERGE"})
    assert resolution.status_code == 501
    assert "not available" in resolution.json()["detail"]


def test_conflicts_use_native_resolution_when_available(monkeypatch, tmp_path) -> None:
    import dhee.mcp_server as mcp_server

    memory = FakeResolvableConflictMemory()
    monkeypatch.setattr(mcp_server, "get_memory_instance", lambda: memory)
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    app = create_app(serve_static=False)
    client = TestClient(app)

    snapshot = client.get("/api/conflicts")
    assert snapshot.status_code == 200
    assert snapshot.json()["supported"] is True
    assert snapshot.json()["resolutionMode"] == "native"

    resolution = client.post("/api/conflicts/conflict-1/resolve", json={"action": "KEEP B"})
    assert resolution.status_code == 200
    assert resolution.json()["ok"] is True
    assert memory.resolved == [("conflict-1", "KEEP B", None, None)]
