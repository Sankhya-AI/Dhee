from __future__ import annotations

from fastapi.testclient import TestClient

from dhee.agent_runtime.server import create_app


def test_elevenlabs_tool_refuses_to_store_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("DHEE_HTTP_TOKEN", raising=False)
    app = create_app(
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
        profile="elevenlabs",
    )
    client = TestClient(app)

    result = client.post(
        "/v1/tools/dhee_memory",
        json={
            "user_id": "user_123",
            "app_id": "elevenlabs:support-agent",
            "action": "remember",
            "content": "My card number is 4111 1111 1111 1111.",
        },
    )
    recall = client.post(
        "/v1/tools/dhee_memory",
        json={
            "user_id": "user_123",
            "app_id": "elevenlabs:support-agent",
            "action": "recall",
            "query": "card number",
        },
    )

    assert result.json()["ok"] is False
    assert result.json()["result"]["error"] == "sensitive_content"
    assert recall.json()["speakable_summary"] == "I could not find a relevant previous memory."
