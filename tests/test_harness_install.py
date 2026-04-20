from __future__ import annotations

import json
from pathlib import Path

from dhee.cli_config import load_config
from dhee.harness.install import disable_harnesses, harness_status, install_harnesses


def test_install_codex_writes_native_config_and_instructions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    results = install_harnesses(harness="codex")
    result = results["codex"]
    assert result.action == "enabled"

    config_path = home / ".codex" / "config.toml"
    override_path = home / ".codex" / "AGENTS.override.md"
    config_text = config_path.read_text(encoding="utf-8")
    assert "[mcp_servers.dhee]" in config_text
    assert 'DHEE_HARNESS = "codex"' in config_text
    assert override_path.exists()
    assert "primary memory and context-router" in override_path.read_text(encoding="utf-8")

    config = load_config()
    assert config["harnesses"]["codex"]["enabled"] is True

    status = harness_status(harness="codex")["codex"]
    assert status["mcp_registered"] is True
    assert status["instructions_present"] is True


def test_install_claude_sets_shared_identity_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    results = install_harnesses(harness="claude_code")
    result = results["claude_code"]
    settings_path = Path(result.path)
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    env = settings["mcpServers"]["dhee"]["env"]
    assert env["DHEE_HARNESS"] == "claude_code"
    assert env["DHEE_USER_ID"] == "default"
    assert env["DHEE_ROUTER"] == "1"


def test_disable_codex_removes_managed_surfaces(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    install_harnesses(harness="codex")
    result = disable_harnesses(harness="codex")["codex"]
    assert result.action == "disabled"

    config_path = home / ".codex" / "config.toml"
    override_path = home / ".codex" / "AGENTS.override.md"
    text = config_path.read_text(encoding="utf-8")
    assert "[mcp_servers.dhee]" not in text
    assert not override_path.exists()
