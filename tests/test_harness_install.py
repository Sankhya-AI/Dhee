from __future__ import annotations

import json
from pathlib import Path

import yaml

from dhee.cli_config import load_config
from dhee.harness.install import disable_harnesses, harness_status, install_harnesses


def test_install_codex_writes_native_config_and_instructions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    results = install_harnesses(harness="codex")
    result = results["codex"]
    assert result.action == "enabled"

    config_path = home / ".codex" / "config.toml"
    agents_path = home / ".codex" / "AGENTS.md"
    config_text = config_path.read_text(encoding="utf-8")
    assert "[mcp_servers.dhee]" in config_text
    assert 'DHEE_HARNESS = "codex"' in config_text
    assert 'DHEE_ROUTER = "1"' in config_text
    assert 'DHEE_CONTEXT_FIRST = "1"' in config_text
    assert 'DHEE_CODEX_NATIVE = "1"' in config_text
    assert 'DHEE_CODEX_NATIVE_LEVEL = "closest_available"' in config_text
    assert 'DHEE_CODEX_NATIVE_SURFACES = "codex_mcp_config,codex_global_agents_md,mcp_server_instructions,codex_session_stream_auto_sync"' in config_text
    assert 'DHEE_CONTEXT_FIRST_TOOLS = "dhee_handoff,dhee_shared_task,dhee_shared_task_results,dhee_inbox,dhee_search_learnings"' in config_text
    assert 'DHEE_SHARED_CONTEXT_FIRST = "1"' in config_text
    assert 'DHEE_ROUTER_TOOLS = "dhee_read,dhee_grep,dhee_bash,dhee_expand_result"' in config_text
    assert agents_path.exists()
    instructions = agents_path.read_text(encoding="utf-8")
    assert "primary memory, context-router" in instructions
    assert "Codex-native surfaces" in instructions
    assert "Dhee syncs Codex session logs opportunistically" in instructions
    assert "call `dhee_handoff`" in instructions
    assert "call `dhee_inbox`" in instructions
    assert "call `dhee_broadcast`" in instructions
    assert "Search promoted learnings with `dhee_search_learnings`" in instructions

    config = load_config()
    assert config["harnesses"]["codex"]["enabled"] is True

    status = harness_status(harness="codex")["codex"]
    assert status["mcp_registered"] is True
    assert status["native"] is True
    assert status["native_level"] == "closest_available"
    assert "codex_session_stream_auto_sync" in status["native_surfaces"]
    assert status["router_env"] == "1"
    assert status["context_first"] == "1"
    assert status["shared_context_first"] == "1"
    assert status["auto_sync"] == "1"
    assert "dhee_search_learnings" in status["context_first_tools"]
    assert status["router_contract"] == "context_first"
    assert "dhee_grep" in status["router_tools"]
    assert status["instructions_present"] is True
    assert status["instructions_path"].endswith("AGENTS.md")


def test_install_codex_backs_up_existing_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    config_path = codex_dir / "config.toml"
    config_path.write_text('model = "gpt-5"\n', encoding="utf-8")

    result = install_harnesses(harness="codex")["codex"]

    backup = result.details["backup"]
    assert backup is not None
    assert Path(backup).read_text(encoding="utf-8") == 'model = "gpt-5"\n'
    assert 'model = "gpt-5"' in config_path.read_text(encoding="utf-8")


def test_legacy_mcp_codex_config_path_uses_native_installer(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (home / ".codex").mkdir(parents=True)

    from dhee.cli_mcp import _configure_codex

    assert _configure_codex({"provider": "gemini"}) == "configured"

    config_text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert 'DHEE_CODEX_NATIVE = "1"' in config_text
    assert 'DHEE_CONTEXT_FIRST = "1"' in config_text
    assert (home / ".codex" / "AGENTS.md").exists()


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
    assert env["DHEE_AUTO_CONTINUITY"] == "1"
    assert env["DHEE_SHARED_CONTEXT_FIRST"] == "1"


def test_disable_codex_removes_managed_surfaces(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    install_harnesses(harness="codex")
    result = disable_harnesses(harness="codex")["codex"]
    assert result.action == "disabled"

    config_path = home / ".codex" / "config.toml"
    agents_path = home / ".codex" / "AGENTS.md"
    text = config_path.read_text(encoding="utf-8")
    assert "[mcp_servers.dhee]" not in text
    assert not agents_path.exists()


def test_install_codex_migrates_legacy_override_to_agents_md(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    legacy_path = codex_dir / "AGENTS.override.md"
    legacy_path.write_text(
        "<!-- DHEE:START -->\nold managed block\n<!-- DHEE:END -->\n",
        encoding="utf-8",
    )

    install_harnesses(harness="codex")

    agents_path = codex_dir / "AGENTS.md"
    assert "Dhee Native Integration" in agents_path.read_text(encoding="utf-8")
    assert not legacy_path.exists()


def test_install_all_auto_configures_detected_hermes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    hermes_home = home / ".hermes"
    (hermes_home / "memories").mkdir(parents=True)
    (hermes_home / "config.yaml").write_text("memory:\n  provider: honcho\n", encoding="utf-8")
    (hermes_home / "memories" / "USER.md").write_text("User prefers concise answers.\n", encoding="utf-8")

    results = install_harnesses(harness="all")

    hermes = results["hermes"]
    assert hermes.action == "enabled"
    assert (hermes_home / "plugins" / "dhee" / "__init__.py").exists()
    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["memory"]["provider"] == "dhee"
    assert hermes.details["imported_learnings"] == 1

    status = harness_status(harness="hermes")["hermes"]
    assert status["installed"] is True
    assert status["mcp_registered"] is True
