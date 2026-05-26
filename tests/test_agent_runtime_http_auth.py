from __future__ import annotations

from fastapi.testclient import TestClient

from dhee.agent_runtime.server import create_app


def test_http_sidecar_uses_bearer_token_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_HTTP_TOKEN", "dev-token")
    app = create_app(data_dir=tmp_path / "dhee", in_memory=True, offline=True)
    client = TestClient(app)

    unauthorized = client.post(
        "/v1/tools/dhee_memory",
        json={"action": "recall", "query": "anything"},
    )
    authorized = client.post(
        "/v1/tools/dhee_memory",
        headers={"Authorization": "Bearer dev-token"},
        json={"action": "recall", "query": "anything"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
