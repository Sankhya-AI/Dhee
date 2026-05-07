import json
import yaml

from dhee.integrations.hermes import detect_hermes, install_provider, provider_status, sync_hermes
from dhee.integrations.hermes_provider import DheeHermesMemoryProvider


def test_hermes_provider_lifecycle_and_tools(tmp_path):
    hermes_home = tmp_path / "hermes"
    repo = tmp_path / "repo"
    repo.mkdir()
    provider = DheeHermesMemoryProvider()

    assert provider.name == "dhee"
    assert provider.is_available()
    provider.initialize(
        "session-1",
        hermes_home=str(hermes_home),
        dhee_data_dir=str(tmp_path / "dhee"),
        repo=str(repo),
        offline=True,
        in_memory=True,
        agent_identity="coder",
    )

    assert "promoted Dhee learnings" in provider.system_prompt_block()
    assert {schema["name"] for schema in provider.get_tool_schemas()} >= {
        "dhee_remember",
        "dhee_search",
        "dhee_submit_learning",
        "dhee_search_learnings",
    }

    raw = provider.handle_tool_call(
        "dhee_submit_learning",
        {
            "title": "Prefer focused pytest",
            "body": "Run the smallest relevant pytest target before the full suite.",
            "kind": "heuristic",
            "task_type": "testing",
        },
    )
    payload = json.loads(raw)
    learning_id = payload["learning"]["id"]
    assert payload["learning"]["status"] == "candidate"
    assert "Focused" not in provider.prefetch("pytest")

    provider._exchange.promote(learning_id, approved_by="test")
    assert "Learned Playbooks" in provider.prefetch("pytest")

    provider.sync_turn("User asks for tests", "Assistant runs pytest")
    provider.on_memory_write("add", "memory", "Use pytest -q for targeted checks")
    provider.on_session_end([
        {"role": "user", "content": "Please fix the parser"},
        {"role": "assistant", "content": "Fixed parser and ran tests"},
    ])
    provider.shutdown()


def test_hermes_install_enable_backs_up_config(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text("memory:\n  provider: honcho\n", encoding="utf-8")

    result = install_provider(
        hermes_home_path=str(hermes_home),
        enable=True,
        dhee_data_dir=str(tmp_path / "dhee"),
        offline=True,
    )

    assert result["enabled"] is True
    assert result["backup"]
    assert (hermes_home / "plugins" / "dhee" / "__init__.py").exists()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["memory"]["provider"] == "dhee"

    status = provider_status(str(hermes_home))
    assert status["plugin_installed"] is True
    assert status["enabled"] is True


def test_hermes_install_can_sync_and_promote_existing_progress(tmp_path):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "memories").mkdir(parents=True)
    (hermes_home / "memories" / "MEMORY.md").write_text("User prefers focused, minimal output.\n", encoding="utf-8")
    skill_dir = hermes_home / "skills" / "agent-made"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Agent Made\nRun smoke tests before broad tests.\n", encoding="utf-8")

    result = install_provider(
        hermes_home_path=str(hermes_home),
        enable=True,
        dhee_data_dir=str(tmp_path / "dhee"),
        sync_existing=True,
        promote_imported=True,
    )

    assert result["sync"]["imported_count"] == 2
    assert result["sync"]["promote"] is True
    status = provider_status(str(hermes_home))
    assert status["enabled"] is True
    rows = sync_hermes(
        hermes_home_path=str(hermes_home),
        dry_run=True,
        dhee_data_dir=str(tmp_path / "dhee"),
        promote=True,
    )
    assert rows["skipped_count"] == 2


def test_hermes_sync_dry_run_imports_without_writing(tmp_path):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "memories").mkdir(parents=True)
    (hermes_home / "memories" / "USER.md").write_text("User prefers concise replies.\n", encoding="utf-8")
    data_dir = tmp_path / "dhee"

    result = sync_hermes(
        hermes_home_path=str(hermes_home),
        dry_run=True,
        dhee_data_dir=str(data_dir),
    )

    assert result["imported_count"] == 1
    assert not (data_dir / "learnings" / "learnings.jsonl").exists()


def test_detect_hermes_uses_home_config_without_importing_hermes(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("memory:\n  provider: honcho\n", encoding="utf-8")
    (hermes_home / "sessions").mkdir()
    (hermes_home / "sessions" / "session_demo.json").write_text("{}", encoding="utf-8")

    detected = detect_hermes(str(hermes_home))

    assert detected["installed"] is True
    assert detected["active_provider"] == "honcho"
    assert detected["session_count"] == 1
