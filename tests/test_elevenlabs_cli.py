from __future__ import annotations

import json

from dhee import cli


def test_elevenlabs_init_cli_outputs_profile_json(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "dhee",
            "elevenlabs",
            "init",
            "--public-url",
            "https://memory.example.com",
            "--json",
        ],
    )

    cli.main()
    data = json.loads(capsys.readouterr().out)

    assert "dhee_context" in data["dynamic_variables"]
    assert "dhee_app_id" in data["dynamic_variables"]
    assert data["server_tool"]["name"] == "dhee_memory"
    assert data["server_tool"]["url"] == "https://memory.example.com/v1/tools/dhee_memory"
    assert data["post_call_webhook"] == "https://memory.example.com/v1/webhooks/elevenlabs/post_call"


def test_elevenlabs_doctor_distinguishes_protected_unsigned_webhook(monkeypatch, capsys):
    monkeypatch.delenv("ELEVENLABS_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("DHEE_HTTP_TOKEN", "dev-token")

    def fake_http_check(url, **kwargs):
        if url.endswith("/v1/webhooks/elevenlabs/post_call"):
            return {"ok": True, "status": 401, "error": "Unauthorized"}
        return {"ok": True, "status": 200}

    monkeypatch.setattr(cli, "_http_check", fake_http_check)
    monkeypatch.setattr(
        "sys.argv",
        ["dhee", "elevenlabs", "doctor", "--url", "https://memory.example.com", "--json"],
    )

    cli.main()
    data = json.loads(capsys.readouterr().out)

    assert data["elevenlabs_webhook_secret"] is False
    assert data["production_webhook_signing_ready"] is False
    assert data["post_call_webhook_endpoint"]["ok"] is True
    assert data["post_call_webhook_endpoint"]["protected"] is True
