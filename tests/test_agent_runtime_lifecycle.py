from __future__ import annotations

from fastapi.testclient import TestClient

from dhee.agent_runtime.run import Run
from dhee.agent_runtime.server import create_app


def test_http_sidecar_removes_completed_runs_from_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("DHEE_HTTP_TOKEN", raising=False)
    app = create_app(data_dir=tmp_path / "dhee", in_memory=True, offline=True)
    client = TestClient(app)

    started = client.post(
        "/v1/runs/start",
        json={"user_id": "user_123", "app_id": "test-agent", "task": "test"},
    )
    run_id = started.json()["run_id"]
    registry = app.state.dhee_run_registry

    assert run_id in registry._runs

    finished = client.post(
        f"/v1/runs/{run_id}/finish",
        json={
            "user_id": "user_123",
            "app_id": "test-agent",
            "summary": "Finished test run.",
        },
    )

    assert finished.status_code == 200
    assert run_id not in registry._runs


def test_http_sidecar_does_not_cache_anonymous_tool_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("DHEE_HTTP_TOKEN", raising=False)
    app = create_app(data_dir=tmp_path / "dhee", in_memory=True, offline=True)
    client = TestClient(app)

    response = client.post(
        "/v1/tools/dhee_memory",
        json={"user_id": "user_123", "app_id": "test-agent", "action": "recall", "query": "anything"},
    )

    assert response.status_code == 200
    assert app.state.dhee_run_registry._runs == {}


class _FakePlugin:
    def __init__(self):
        self.session_end_calls = []
        self.checkpoint_calls = []

    def session_end(self, **kwargs):
        self.session_end_calls.append(kwargs)
        return {"ok": True}

    def checkpoint(self, **kwargs):
        self.checkpoint_calls.append(kwargs)
        return {"ok": True}


def test_run_finish_uses_session_end_and_persists_runtime_metadata():
    plugin = _FakePlugin()
    run = Run(
        plugin=plugin,
        user_id="user_123",
        app_id="elevenlabs:support-agent",
        task="voice support call",
        run_id="run_123",
        metadata={},
    )
    run.events.append({"type": "voice.user_transcript"})

    result = run.finish(
        outcome="failed",
        summary="Call ended unexpectedly.",
        metadata={"conversation_id": "conv_123"},
    )

    assert result == {
        "ok": True,
        "metadata": {
            "app_id": "elevenlabs:support-agent",
            "run_id": "run_123",
            "events_observed": 1,
            "conversation_id": "conv_123",
        },
    }
    assert plugin.checkpoint_calls == []
    assert plugin.session_end_calls[0]["status"] == "failed"
    summary = plugin.session_end_calls[0]["summary"]
    assert "Call ended unexpectedly." in summary
    assert "elevenlabs:support-agent" in summary
    assert "conv_123" in summary
