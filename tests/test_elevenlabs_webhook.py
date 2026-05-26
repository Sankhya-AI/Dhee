from __future__ import annotations

from fastapi.testclient import TestClient

from dhee.agent_runtime.server import create_app


def test_elevenlabs_post_call_webhook_stores_durable_voice_memory(tmp_path, monkeypatch):
    monkeypatch.delenv("DHEE_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("ELEVENLABS_WEBHOOK_SECRET", raising=False)
    app = create_app(
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
        profile="elevenlabs",
        allow_unsigned_webhooks=True,
    )
    client = TestClient(app)

    start = client.post(
        "/v1/runs/start",
        json={
            "user_id": "user_123",
            "app_id": "elevenlabs:support-agent",
            "task": "voice support call",
            "channel": "voice",
        },
    )
    run_id = start.json()["run_id"]

    webhook = client.post(
        "/v1/webhooks/elevenlabs/post_call",
        json={
            "type": "post_call_transcription",
            "data": {
                "conversation_id": "conv_123",
                "agent_id": "support-agent",
                "conversation_initiation_client_data": {
                    "dynamic_variables": {
                        "dhee_user_id": "user_123",
                        "dhee_run_id": run_id,
                    }
                },
                "transcript": [
                    {
                        "role": "user",
                        "message": "Please remember I prefer WhatsApp follow-up.",
                    },
                    {"role": "agent", "message": "I can do that."},
                ],
                "analysis": {
                    "transcript_summary": "User set WhatsApp as their follow-up preference.",
                    "call_successful": True,
                },
                "status": "done",
            },
        },
    )
    recall = client.post(
        "/v1/tools/dhee_memory",
        json={
            "user_id": "user_123",
            "app_id": "elevenlabs:support-agent",
            "run_id": run_id,
            "action": "recall",
            "query": "follow-up preference",
        },
    )

    assert webhook.status_code == 200
    assert webhook.json() == {"status": "received"}
    assert "WhatsApp" in recall.json()["speakable_summary"]


def test_elevenlabs_post_call_webhook_rejects_unsigned_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DHEE_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("ELEVENLABS_WEBHOOK_SECRET", raising=False)
    app = create_app(
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
        profile="elevenlabs",
    )
    client = TestClient(app)

    response = client.post(
        "/v1/webhooks/elevenlabs/post_call",
        json={"type": "post_call_transcription", "data": {}},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "Unsigned ElevenLabs webhook rejected"
