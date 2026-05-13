from __future__ import annotations

import json


def test_token_router_demo_shows_context_firewall_value():
    from dhee.demo import token_router_demo

    report = token_router_demo()

    assert report["format"] == "dhee_token_router_demo"
    assert "context firewall" in report["positioning"].lower()
    assert report["aggregate"]["cases"] == 3
    assert report["aggregate"]["saved_pct"] > 50
    assert {case["surface"] for case in report["cases"]} == {"dhee_bash", "dhee_read"}
    assert all("dhee_expand_result" in case["expand"] for case in report["cases"])


def test_cli_demo_token_router_json(monkeypatch, capsys):
    from dhee import cli

    monkeypatch.setattr(
        "sys.argv",
        ["dhee", "demo", "token-router", "--json"],
    )
    cli.main()
    data = json.loads(capsys.readouterr().out)

    assert data["aggregate"]["saved_pct"] > 50
    assert data["cases"][0]["ptr"].startswith("B-demo")
