from __future__ import annotations

import json

from dhee import cli


def _run_cli(monkeypatch, capsys, *argv):
    monkeypatch.setattr("sys.argv", ["dhee", *argv])
    cli.main()
    return capsys.readouterr()


def test_install_all_and_status_round_trip(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    out = _run_cli(monkeypatch, capsys, "install", "--harness", "all")
    assert "Claude Code: enabled" in out.out
    assert "Codex: enabled" in out.out

    status = _run_cli(monkeypatch, capsys, "harness", "status", "--json")
    data = json.loads(status.out)
    assert data["claude_code"]["enabled_in_config"] is True
    assert data["claude_code"]["mcp_registered"] is True
    assert data["codex"]["enabled_in_config"] is True
    assert data["codex"]["mcp_registered"] is True


def test_disable_codex_via_cli(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    _run_cli(monkeypatch, capsys, "install", "--harness", "all")
    out = _run_cli(monkeypatch, capsys, "harness", "disable", "--harness", "codex", "--json")
    data = json.loads(out.out)
    assert data["codex"]["action"] == "disabled"

    status = _run_cli(monkeypatch, capsys, "status", "--json")
    summary = json.loads(status.out)
    assert summary["native_harnesses"]["codex"]["enabled_in_config"] is False
    assert summary["native_harnesses"]["claude_code"]["enabled_in_config"] is True
