import json
import yaml

from dhee import DheePlugin
from dhee.core.learnings import LearningExchange
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
    assert (hermes_home / "plugins" / "memory" / "dhee" / "__init__.py").exists()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["memory"]["provider"] == "dhee"

    status = provider_status(str(hermes_home))
    assert status["plugin_installed"] is True
    assert status["legacy_plugin_installed"] is False
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
    assert result["sync"]["promoted_count"] == 1
    assert result["sync"]["candidate_count"] == 1
    status = provider_status(str(hermes_home))
    assert status["enabled"] is True
    plugin = DheePlugin(data_dir=tmp_path / "dhee", in_memory=True, offline=True)
    promoted = plugin.search_learnings(status="promoted", limit=10)
    candidates = plugin.search_learnings(status="candidate", limit=10)
    assert any(row["title"] == "Hermes memories/MEMORY.md" for row in promoted)
    assert any(row["title"] == "Hermes skill: agent-made" for row in candidates)
    rows = sync_hermes(
        hermes_home_path=str(hermes_home),
        dry_run=True,
        dhee_data_dir=str(tmp_path / "dhee"),
        promote=True,
    )
    assert rows["skipped_count"] == 2


def test_hermes_import_policy_keeps_soul_sessions_and_skills_gated(tmp_path):
    hermes_home = tmp_path / "hermes"
    data_dir = tmp_path / "dhee"
    hermes_home.mkdir()
    (hermes_home / "SOUL.md").write_text("Be a concise terminal coding agent.\n", encoding="utf-8")
    skill_dir = hermes_home / "skills" / "agent-made"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Agent Made\nInspect traces before patching.\n", encoding="utf-8")
    sessions_dir = hermes_home / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "session_demo.json").write_text(
        json.dumps(
            {
                "id": "session_demo",
                "title": "Fixed parser",
                "messages": [
                    {"role": "user", "content": "Fix the parser regression."},
                    {"role": "assistant", "content": "Fixed it by adding a focused fixture."},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = install_provider(
        hermes_home_path=str(hermes_home),
        enable=True,
        dhee_data_dir=str(data_dir),
        sync_existing=True,
        promote_imported=True,
    )

    assert result["sync"]["imported_count"] == 3
    assert result["sync"]["promoted_count"] == 0
    assert result["sync"]["candidate_count"] == 3
    plugin = DheePlugin(data_dir=data_dir, in_memory=True, offline=True)
    assert plugin.search_learnings(status="promoted", limit=10) == []
    candidates = plugin.search_learnings(status="candidate", include_candidates=True, limit=10)
    assert {row["title"] for row in candidates} == {
        "Hermes SOUL.md",
        "Hermes skill: agent-made",
        "Fixed parser",
    }


def test_hermes_import_policy_migrates_old_blanket_promotions(tmp_path):
    hermes_home = tmp_path / "hermes"
    data_dir = tmp_path / "dhee"
    hermes_home.mkdir()
    (hermes_home / "SOUL.md").write_text("Be terse and never explain tradeoffs.\n", encoding="utf-8")

    exchange = LearningExchange(data_dir / "learnings")
    exchange.import_hermes_home(hermes_home, promote=False)
    stale = exchange.list()[0]
    stale.status = "promoted"
    stale.promoted_at = 1.0
    stale.metadata["approved_by"] = "hermes_import"
    exchange._upsert(stale)

    result = install_provider(
        hermes_home_path=str(hermes_home),
        enable=True,
        dhee_data_dir=str(data_dir),
        sync_existing=True,
        promote_imported=True,
    )

    assert result["sync"]["updated_policy_count"] == 1
    plugin = DheePlugin(data_dir=data_dir, in_memory=True, offline=True)
    assert plugin.search_learnings(status="promoted", limit=10) == []
    candidates = plugin.search_learnings(status="candidate", include_candidates=True, limit=10)
    assert candidates[0]["title"] == "Hermes SOUL.md"


def test_hermes_import_policy_preserves_user_approved_promotions(tmp_path):
    hermes_home = tmp_path / "hermes"
    data_dir = tmp_path / "dhee"
    hermes_home.mkdir()
    (hermes_home / "SOUL.md").write_text("Always include the exact repo path in handoffs.\n", encoding="utf-8")

    exchange = LearningExchange(data_dir / "learnings")
    exchange.import_hermes_home(hermes_home, promote=False)
    approved = exchange.list()[0]
    approved.status = "promoted"
    approved.promoted_at = 1.0
    approved.metadata["approved_by"] = "cli"
    exchange._upsert(approved)

    result = install_provider(
        hermes_home_path=str(hermes_home),
        enable=True,
        dhee_data_dir=str(data_dir),
        sync_existing=True,
        promote_imported=True,
    )

    assert result["sync"]["updated_policy_count"] == 0
    plugin = DheePlugin(data_dir=data_dir, in_memory=True, offline=True)
    promoted = plugin.search_learnings(status="promoted", limit=10)
    assert promoted[0]["title"] == "Hermes SOUL.md"


def test_hermes_imported_progress_reaches_dhee_context_and_hermes_prefetch(tmp_path):
    hermes_home = tmp_path / "hermes"
    data_dir = tmp_path / "dhee"
    repo = tmp_path / "repo"
    repo.mkdir()
    (hermes_home / "memories").mkdir(parents=True)
    (hermes_home / "memories" / "MEMORY.md").write_text(
        "For parser regressions, inspect the failing fixture before broad refactors.\n",
        encoding="utf-8",
    )

    result = install_provider(
        hermes_home_path=str(hermes_home),
        enable=True,
        dhee_data_dir=str(data_dir),
        sync_existing=True,
        promote_imported=True,
    )
    assert result["sync"]["imported_count"] == 1

    codex_side = DheePlugin(data_dir=data_dir, in_memory=True, offline=True)
    context = codex_side.context("parser fixture regression", repo=str(repo))
    prompt = codex_side._render_system_prompt(context)
    assert "### Learned Playbooks" in prompt
    assert "inspect the failing fixture" in prompt

    hermes_side = DheeHermesMemoryProvider()
    hermes_side.initialize(
        "session-import",
        hermes_home=str(hermes_home),
        dhee_data_dir=str(data_dir),
        repo=str(repo),
        offline=True,
        in_memory=True,
    )
    prefetch = hermes_side.prefetch("parser fixture regression")
    assert "### Learned Playbooks" in prefetch
    assert "inspect the failing fixture" in prefetch


def test_codex_promoted_learning_reaches_hermes_prefetch(tmp_path):
    data_dir = tmp_path / "dhee"
    repo = tmp_path / "repo"
    repo.mkdir()
    codex_side = DheePlugin(data_dir=data_dir, in_memory=True, offline=True)
    candidate = codex_side.submit_learning(
        title="Use router grep before raw search",
        body="On large repositories, ask Dhee for routed grep output before reading raw full files.",
        kind="heuristic",
        source_agent_id="codex",
        source_harness="codex",
        task_type="codebase_search",
        repo=str(repo),
    )
    codex_side.promote_learning(candidate["id"], repo=str(repo), approved_by="test")

    hermes_side = DheeHermesMemoryProvider()
    hermes_side.initialize(
        "session-codex",
        hermes_home=str(tmp_path / "hermes"),
        dhee_data_dir=str(data_dir),
        repo=str(repo),
        offline=True,
        in_memory=True,
    )

    prefetch = hermes_side.prefetch("large repo search")
    assert "### Learned Playbooks" in prefetch
    assert "Use router grep before raw search" in prefetch


def test_hermes_session_end_creates_candidate_without_auto_injection(tmp_path):
    provider = DheeHermesMemoryProvider()
    provider.initialize(
        "session-end",
        hermes_home=str(tmp_path / "hermes"),
        dhee_data_dir=str(tmp_path / "dhee"),
        offline=True,
        in_memory=True,
    )
    messages = [
        {"role": "user", "content": "Use the wasm fixture first for this parser bug."},
        {"role": "assistant", "content": "Fixed the parser by reproducing against the wasm fixture."},
    ]

    provider.on_session_end(messages)
    rows = provider._exchange.search(
        query="wasm fixture parser",
        status="candidate",
        include_candidates=True,
        limit=5,
    )
    assert rows
    assert rows[0]["source_harness"] == "hermes"
    assert rows[0]["task_type"] == "hermes_session"
    assert "### Learned Playbooks" not in provider.prefetch("wasm fixture parser")

    provider._exchange.promote(rows[0]["id"], approved_by="test")
    assert "### Learned Playbooks" in provider.prefetch("wasm fixture parser")


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
