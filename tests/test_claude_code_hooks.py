"""Tests for the Dhee Claude Code hook system.

Covers: XML renderer, privacy filter, signal extractor, installer (incl.
v3.3.0 → v3.3.1 legacy migration), dispatch handlers.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from dhee.hooks.claude_code.install import (
    HOOK_EVENTS,
    LEGACY_EVENTS,
    InstallResult,
    _all_installed,
    ensure_installed,
    install_hooks,
    uninstall_hooks,
)
from dhee.hooks.claude_code.privacy import filter_secrets
from dhee.hooks.claude_code.renderer import (
    CHARS_PER_TOKEN,
    DEFAULT_TOKEN_BUDGET,
    estimate_tokens,
    render_context,
)
from dhee.hooks.claude_code.assembler import (
    AssembledContext,
    DocMatch,
    assemble_docs_only,
)
from dhee.hooks.claude_code.chunker import (
    Chunk,
    chunk_markdown,
    sha256_of,
)
from dhee.hooks.claude_code.ingest import (
    IngestResult,
    is_stale,
)
from dhee.hooks.claude_code.migrate import (
    PurgeResult,
    _looks_like_legacy_bash_success,
    _looks_like_self_referential,
    purge_legacy_noise,
)
from dhee.hooks.claude_code.signal import (
    extract_signal,
    has_cognition_signal,
    is_self_referential,
)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _extract_xml(rendered: str) -> ET.Element:
    """Extract and parse the <dhee> XML block from rendered output."""
    match = re.search(r"<dhee[\s>]", rendered)
    if not match:
        raise ValueError("No <dhee> block found")
    return ET.fromstring(rendered[match.start():])


def _rich_ctx() -> dict:
    return {
        "last_session": {
            "summary": "Fixed flaky auth test",
            "status": "completed",
            "decisions": ["mock datetime.utcnow"],
            "files_touched": ["tests/test_auth.py"],
            "todos": ["verify with CI"],
        },
        "performance": [
            {
                "task_type": "bug_fix",
                "total_attempts": 3,
                "best_score": 0.9,
                "avg_score": 0.82,
                "trend": 0.05,
            }
        ],
        "insights": [
            {"content": "freezegun works", "task_type": "bug_fix"},
        ],
        "intentions": [
            {"content": "prefer freezegun", "trigger_keywords": ["flaky", "time"]},
        ],
        "memories": [
            {"memory": "JWT uses 60-min expiry", "score": 0.57},
            {"memory": "flaky test fixed by mocking clock", "score": 0.83},
        ],
        "beliefs": [
            {"claim": "pytest-asyncio used", "belief_type": "fact", "confidence": 0.9},
        ],
        "policies": [
            {"name": "verify_first", "description": "Run tests first", "confidence": 0.87},
        ],
        "warnings": ["Repeated failure detected"],
    }


class TestRenderer:
    def test_empty_context_returns_empty_string(self):
        """No content → no injection. Don't emit a bare <dhee/>."""
        assert render_context({}) == ""

    def test_only_empty_sections_returns_empty_string(self):
        assert render_context({"memories": [], "insights": [], "warnings": []}) == ""

    def test_rich_context_produces_all_sections(self):
        xml = render_context(_rich_ctx())
        assert xml.startswith("<dhee>")
        # Flat format — no wrapper tags, check item tags directly
        for tag in ["session", "perf", "i", "intent", "m", "b", "p", "w"]:
            assert f"<{tag}" in xml, f"Missing tag: {tag}"

    def test_no_header_no_wrappers(self):
        """v3.4 killed the header and wrapper tags to save tokens."""
        xml = render_context(_rich_ctx())
        assert "Dhee context" not in xml
        assert "<docs>" not in xml
        assert "<insights>" not in xml
        assert "<memories>" not in xml
        assert "<intentions>" not in xml

    def test_task_description_in_root_attribute(self):
        ctx = {"insights": [{"content": "freezegun works", "task_type": "bug_fix"}]}
        xml = render_context(ctx, task_description="fix auth")
        assert 'task="fix auth"' in xml

    def test_memories_sorted_by_score_descending(self):
        ctx = {
            "insights": [{"content": "pin anchor section"}],
            "memories": [
                {"memory": "low", "score": 0.1},
                {"memory": "high", "score": 0.9},
                {"memory": "mid", "score": 0.5},
            ],
        }
        xml = render_context(ctx)
        root = _extract_xml(xml)
        scores = [float(m.get("s")) for m in root.findall("m")]
        assert scores == sorted(scores, reverse=True)

    def test_xml_injection_escaped(self):
        ctx = {
            "memories": [{"memory": '<script>alert("xss")</script> & co', "score": 0.5}],
            "warnings": ['contains "quoted" <text>'],
        }
        xml = render_context(ctx)
        assert "<script>" not in xml
        root = _extract_xml(xml)
        mem = root.find("m")
        assert mem is not None
        assert '<script>alert("xss")</script>' in mem.text

    def test_budget_drops_low_priority_sections(self):
        ctx = _rich_ctx()
        ctx["memories"] = [{"memory": f"mem {i} " * 20, "score": 0.5} for i in range(20)]
        tight = render_context(ctx, max_tokens=200)
        full = render_context(ctx, max_tokens=3000)
        assert estimate_tokens(tight) < estimate_tokens(full)
        assert "<session" in tight

    def test_session_block_has_highest_priority(self):
        ctx = _rich_ctx()
        ctx["memories"] = [{"memory": f"m{i} " * 50, "score": 0.5} for i in range(50)]
        tight = render_context(ctx, max_tokens=150)
        assert "<session" in tight

    def test_typed_cognition_ranked_above_raw_memories(self):
        """Insights/beliefs survive budget cuts that drop raw memories."""
        ctx = {
            "insights": [{"content": "insight one"}],
            "beliefs": [{"claim": "belief one", "confidence": 0.8}],
            "memories": [{"memory": "m " * 100, "score": 0.9}],
        }
        tight = render_context(ctx, max_tokens=180)
        assert "<i" in tight
        assert "<b" in tight

    def test_estimate_tokens_monotone(self):
        assert estimate_tokens("abc") <= estimate_tokens("abc" * 100)

    def test_intention_triggers_attribute(self):
        ctx = {"intentions": [{"content": "use freezegun", "trigger_keywords": ["flaky", "time"]}]}
        xml = render_context(ctx)
        root = _extract_xml(xml)
        intent = root.find("intent")
        assert intent is not None
        assert intent.get("triggers") == "flaky,time"
        assert intent.text == "use freezegun"

    def test_performance_attributes(self):
        ctx = {"performance": [{"task_type": "bug_fix", "total_attempts": 3, "best_score": 0.9, "avg_score": 0.82, "trend": 0.05}]}
        xml = render_context(ctx)
        root = _extract_xml(xml)
        row = root.find("perf")
        assert row is not None
        assert row.get("type") == "bug_fix"
        assert row.get("n") == "3"

    def test_well_formed_xml(self):
        xml = render_context(_rich_ctx(), task_description="test task")
        _extract_xml(xml)

    def test_rich_context_under_default_budget(self):
        xml = render_context(_rich_ctx())
        assert estimate_tokens(xml) <= DEFAULT_TOKEN_BUDGET

    def test_session_summary_truncated(self):
        ctx = {"last_session": {"summary": "x" * 500, "status": "active"}}
        xml = render_context(ctx)
        assert "x" * 201 not in xml


# ---------------------------------------------------------------------------
# Privacy filter
# ---------------------------------------------------------------------------


class TestPrivacy:
    @pytest.mark.parametrize(
        "secret",
        [
            "api_key=sk-abc12345678901234567890",
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc",
            "AKIAIOSFODNN7EXAMPLE",
            'password="super_secret_pass123"',
            "ghp_1234567890abcdefghijklmnopqrstuvwxyz12",
            "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
            "sk-proj-abc1234567890123456789",
        ],
    )
    def test_secrets_redacted(self, secret: str):
        assert "[REDACTED]" in filter_secrets(secret)

    @pytest.mark.parametrize(
        "safe",
        [
            "edited tests/test_auth.py",
            "ran: pytest tests/ -v",
            "normal text with no secrets",
            "fixed bug in auth module",
        ],
    )
    def test_safe_text_untouched(self, safe: str):
        assert filter_secrets(safe) == safe

    def test_empty_input(self):
        assert filter_secrets("") == ""


# ---------------------------------------------------------------------------
# Signal extractor
# ---------------------------------------------------------------------------


class TestSignal:
    def test_bash_success_is_not_signal(self):
        """Successful shell commands are transport, not signal. Storing them
        produces the noise loop that v3.3.0 exhibited."""
        assert extract_signal("Bash", {"command": "ls -la"}, "...", True) is None

    def test_bash_failure_is_signal(self):
        result = extract_signal(
            "Bash",
            {"command": "pytest tests/test_auth.py"},
            "FAILED — missing JWT exp claim",
            False,
        )
        assert result is not None
        content, meta = result
        assert "bash failed" in content
        assert "pytest" in content
        assert meta["kind"] == "failure"
        assert meta["tool"] == "Bash"

    def test_edit_success_is_signal(self):
        result = extract_signal(
            "Edit",
            {"file_path": "/src/auth.py"},
            "",
            True,
        )
        assert result is not None
        content, meta = result
        assert "edited /src/auth.py" == content
        assert meta["kind"] == "file_touched"
        assert meta["path"] == "/src/auth.py"

    def test_edit_failure_is_signal(self):
        result = extract_signal(
            "Edit",
            {"file_path": "/src/auth.py"},
            "string not found",
            False,
        )
        assert result is not None
        content, meta = result
        assert content.startswith("failed to edit /src/auth.py")
        assert meta["kind"] == "failure"

    def test_write_multiedit_variants(self):
        for tool in ("Write", "MultiEdit", "NotebookEdit"):
            assert extract_signal(tool, {"file_path": "/a.py"}, "", True) is not None

    def test_read_glob_grep_never_signal(self):
        for tool in ("Read", "Glob", "Grep", "WebFetch", "TodoWrite"):
            assert extract_signal(tool, {"file_path": "/x"}, "", True) is None
            assert extract_signal(tool, {"file_path": "/x"}, "err", False) is None

    def test_empty_tool_name_returns_none(self):
        assert extract_signal("", {}, "", True) is None

    def test_missing_file_path_returns_none(self):
        assert extract_signal("Edit", {}, "", True) is None

    def test_empty_command_returns_none(self):
        assert extract_signal("Bash", {"command": ""}, "err", False) is None

    @pytest.mark.parametrize(
        "cmd",
        [
            "dhee status",
            "python -m dhee.hooks.claude_code PostToolUse",
            "sqlite3 ~/.dhee/handoff.db 'SELECT 1'",
            "cat ~/.claude/settings.json",
            'echo \'{"prompt":"test"}\' | python -m dhee.hooks.claude_code UserPromptSubmit',
        ],
    )
    def test_self_referential_commands_dropped(self, cmd):
        assert is_self_referential(cmd)
        assert extract_signal("Bash", {"command": cmd}, "err", False) is None

    def test_non_self_ref_failure_preserved(self):
        cmd = "pytest tests/test_login.py -v"
        assert not is_self_referential(cmd)
        assert extract_signal("Bash", {"command": cmd}, "err", False) is not None

    def test_secrets_filtered_in_signal_output(self):
        result = extract_signal(
            "Bash",
            {"command": "echo AKIAIOSFODNN7EXAMPLE > /tmp/x"},
            "permission denied",
            False,
        )
        if result is not None:
            content, _ = result
            assert "AKIAIOSFODNN7EXAMPLE" not in content


class TestCognitionSignalGate:
    def test_empty_ctx_no_signal(self):
        assert has_cognition_signal({}) is False

    def test_memories_alone_not_signal(self):
        """Raw memories don't qualify — they're observations. Only typed
        cognition (insights/intentions/beliefs/policies/session/performance)
        should trigger auto-injection."""
        assert has_cognition_signal({"memories": [{"memory": "x", "score": 0.9}]}) is False

    def test_episodes_alone_not_signal(self):
        assert has_cognition_signal({"episodes": [{"summary": "x"}]}) is False

    @pytest.mark.parametrize(
        "key,value",
        [
            ("insights", [{"content": "x"}]),
            ("intentions", [{"content": "x"}]),
            ("performance", [{"task_type": "bug_fix"}]),
            ("beliefs", [{"claim": "x"}]),
            ("policies", [{"name": "x"}]),
            ("warnings", ["oops"]),
            ("last_session", {"summary": "x"}),
        ],
    )
    def test_typed_layer_triggers_signal(self, key, value):
        assert has_cognition_signal({key: value}) is True


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


class TestInstaller:
    def _fake_settings(self, tmpdir: Path) -> Path:
        return tmpdir / "settings.json"

    def test_fresh_install_creates_file(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            result = install_hooks()
            assert result.created
            assert fake.exists()
            settings = json.loads(fake.read_text())
            assert "SessionStart" in settings["hooks"]
            assert "Stop" in settings["hooks"]

    def test_user_prompt_submit_is_installed(self, tmp_path):
        """v3.3.1 revived UserPromptSubmit for doc-chunk retrieval (not raw
        memory recall as in v3.3.0). It must be present."""
        fake = self._fake_settings(tmp_path)
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            settings = json.loads(fake.read_text())
            assert "UserPromptSubmit" in settings.get("hooks", {})

    def test_all_events_registered(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            result = install_hooks()
            settings = json.loads(fake.read_text())
            for event in result.events:
                assert event in settings["hooks"]
                cmd = settings["hooks"][event][0]["hooks"][0]["command"]
                assert "dhee.hooks.claude_code" in cmd
                assert event in cmd

    def test_idempotent_reinstall(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            result2 = install_hooks()
            assert result2.already_installed

    def test_preserves_existing_hooks(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        fake.write_text(json.dumps({
            "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "other-tool"}]}]},
            "permissions": {"allow": ["Read"]},
        }))
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            settings = json.loads(fake.read_text())
            assert "permissions" in settings
            ptu = settings["hooks"]["PreToolUse"]
            assert len(ptu) == 1
            assert ptu[0]["hooks"][0]["command"] == "other-tool"

    def test_post_tool_use_has_matcher(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            settings = json.loads(fake.read_text())
            assert settings["hooks"]["PostToolUse"][0]["matcher"] == "Edit|Write|MultiEdit|Bash"

    def test_uninstall_removes_dhee_hooks(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            assert uninstall_hooks()
            settings = json.loads(fake.read_text())
            assert settings.get("hooks", {}) == {}

    def test_uninstall_preserves_other_hooks(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        fake.write_text(json.dumps({
            "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "other-tool"}]}]},
        }))
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            uninstall_hooks()
            settings = json.loads(fake.read_text())
            ptu = settings["hooks"]["PreToolUse"]
            assert len(ptu) == 1
            assert ptu[0]["hooks"][0]["command"] == "other-tool"

    def test_force_overwrites(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            result = install_hooks(force=True)
            assert result.updated
            assert result.backed_up is not None

    def test_backup_created(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        fake.write_text("{}")
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            result = install_hooks()
            assert result.backed_up is not None
            assert result.backed_up.exists()

    def test_ensure_installed_returns_result_on_first_call(self, tmp_path):
        fake = self._fake_settings(tmp_path)
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            result = ensure_installed()
            assert result.created or result.updated or result.already_installed

    def test_all_six_events_registered(self, tmp_path):
        """v3.3.1 installs all 6 lifecycle events, including UserPromptSubmit
        (which is now doc-chunk retrieval, not raw memory noise)."""
        fake = self._fake_settings(tmp_path)
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            settings = json.loads(fake.read_text())
            for event in HOOK_EVENTS:
                assert event in settings["hooks"], f"Missing: {event}"


# ---------------------------------------------------------------------------
# Dispatch handlers (unit-level, no real Dhee)
# ---------------------------------------------------------------------------


class TestDispatchHandlers:
    def test_post_tool_edit_stores_file_path(self):
        from dhee.hooks.claude_code.__main__ import handle_post_tool

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock:
            mock.return_value.remember.return_value = {"stored": True}
            result = handle_post_tool({
                "tool_name": "Edit",
                "tool_input": {"file_path": "/src/auth.py"},
                "success": True,
            })
            assert result == {}
            mock.return_value.remember.assert_called_once()
            call_args = mock.return_value.remember.call_args
            assert "/src/auth.py" in call_args.kwargs.get("content", "")

    def test_post_tool_ignores_read_tools(self):
        from dhee.hooks.claude_code.__main__ import handle_post_tool

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock:
            result = handle_post_tool({"tool_name": "Read", "tool_input": {}, "success": True})
            assert result == {}
            mock.return_value.remember.assert_not_called()

    def test_post_tool_skips_bash_success(self):
        """The core v3.3.1 fix: successful shell commands are not stored."""
        from dhee.hooks.claude_code.__main__ import handle_post_tool

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock:
            result = handle_post_tool({
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
                "tool_result": "total 4\n...",
                "success": True,
            })
            assert result == {}
            mock.return_value.remember.assert_not_called()

    def test_post_tool_stores_bash_failure(self):
        from dhee.hooks.claude_code.__main__ import handle_post_tool

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock:
            mock.return_value.remember.return_value = {"stored": True}
            handle_post_tool({
                "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/test_auth.py"},
                "tool_result": "FAILED — exp claim missing",
                "success": False,
            })
            mock.return_value.remember.assert_called_once()
            content = mock.return_value.remember.call_args.kwargs.get("content", "")
            assert "bash failed" in content
            assert "pytest" in content

    def test_post_tool_drops_self_referential_bash_failures(self):
        """Even on failure, Dhee-internal commands don't teach us anything
        useful — skip them to prevent pollution loops."""
        from dhee.hooks.claude_code.__main__ import handle_post_tool

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock:
            handle_post_tool({
                "tool_name": "Bash",
                "tool_input": {"command": "sqlite3 ~/.dhee/handoff.db 'SELECT bogus'"},
                "tool_result": "no such column",
                "success": False,
            })
            mock.return_value.remember.assert_not_called()

    def test_post_tool_filters_secrets(self):
        from dhee.hooks.claude_code.__main__ import handle_post_tool

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock:
            mock.return_value.remember.return_value = {"stored": True}
            handle_post_tool({
                "tool_name": "Bash",
                "tool_input": {"command": "echo sk-ant-api03-secret12345678901234567890"},
                "tool_result": "command not found",
                "success": False,
            })
            if mock.return_value.remember.called:
                content = mock.return_value.remember.call_args.kwargs.get("content", "")
                assert "sk-ant-api03" not in content

    def test_user_prompt_empty_returns_empty(self):
        from dhee.hooks.claude_code.__main__ import handle_user_prompt

        assert handle_user_prompt({"prompt": ""}) == {}
        assert handle_user_prompt({}) == {}

    def test_user_prompt_searches_doc_chunks(self):
        """v3.3.1: UserPromptSubmit does doc-chunk retrieval, not raw memory recall."""
        from dhee.hooks.claude_code.__main__ import handle_user_prompt

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock_dhee, \
             patch("dhee.hooks.claude_code.assembler.assemble_docs_only") as mock_assemble:
            from dhee.hooks.claude_code.assembler import DocMatch
            mock_assemble.return_value = [
                DocMatch(text="Always run tests first", source_path="CLAUDE.md",
                         heading_breadcrumb="Testing", score=0.85, chunk_index=0),
            ]
            result = handle_user_prompt({"prompt": "how do I test this?"})
            assert "systemMessage" in result
            assert "Always run tests first" in result["systemMessage"]
            mock_assemble.assert_called_once()

    def test_user_prompt_no_docs_returns_empty(self):
        """When no doc chunks match above threshold, inject nothing."""
        from dhee.hooks.claude_code.__main__ import handle_user_prompt

        with patch("dhee.hooks.claude_code.__main__._get_dhee"), \
             patch("dhee.hooks.claude_code.assembler.assemble_docs_only", return_value=[]):
            result = handle_user_prompt({"prompt": "random question about quantum physics"})
            assert result == {}

    def test_session_start_empty_assembler_returns_empty(self):
        """No docs, no typed cognition → no injection."""
        from dhee.hooks.claude_code.__main__ import handle_session_start
        from dhee.hooks.claude_code.assembler import AssembledContext

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock, \
             patch("dhee.hooks.claude_code.assembler.assemble") as mock_assemble, \
             patch("dhee.hooks.claude_code.ingest.auto_ingest_project"):
            mock_assemble.return_value = AssembledContext(
                doc_matches=[], typed_cognition={},
            )
            result = handle_session_start({"task_description": "fix"})
            assert result == {}

    def test_session_start_with_doc_matches_injects(self):
        from dhee.hooks.claude_code.__main__ import handle_session_start
        from dhee.hooks.claude_code.assembler import AssembledContext, DocMatch

        with patch("dhee.hooks.claude_code.__main__._get_dhee"), \
             patch("dhee.hooks.claude_code.assembler.assemble") as mock_assemble, \
             patch("dhee.hooks.claude_code.ingest.auto_ingest_project"):
            mock_assemble.return_value = AssembledContext(
                doc_matches=[
                    DocMatch(text="Always run pytest before committing",
                             source_path="CLAUDE.md", heading_breadcrumb="Testing",
                             score=0.88, chunk_index=0),
                ],
                typed_cognition={
                    "insights": [{"content": "freezegun beats manual mocking", "task_type": "bug_fix"}],
                },
            )
            result = handle_session_start({"task_description": "fix flaky test"})
            assert "systemMessage" in result
            assert "Always run pytest" in result["systemMessage"]
            assert "freezegun" in result["systemMessage"]
            assert 'task="fix flaky test"' in result["systemMessage"]

    def test_session_start_with_only_cognition_injects(self):
        from dhee.hooks.claude_code.__main__ import handle_session_start
        from dhee.hooks.claude_code.assembler import AssembledContext

        with patch("dhee.hooks.claude_code.__main__._get_dhee"), \
             patch("dhee.hooks.claude_code.assembler.assemble") as mock_assemble, \
             patch("dhee.hooks.claude_code.ingest.auto_ingest_project"):
            mock_assemble.return_value = AssembledContext(
                doc_matches=[],
                typed_cognition={"warnings": ["auth module churned 5x last week"]},
            )
            result = handle_session_start({})
            assert "systemMessage" in result
            assert "auth module" in result["systemMessage"]

    def test_stop_handler_calls_checkpoint(self):
        from dhee.hooks.claude_code.__main__ import handle_stop

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock:
            mock.return_value.checkpoint.return_value = {}
            handle_stop({"summary": "done", "task_type": "bug_fix", "outcome_score": 0.9})
            mock.return_value.checkpoint.assert_called_once()
            kwargs = mock.return_value.checkpoint.call_args.kwargs
            assert kwargs["summary"] == "done"
            assert kwargs["task_type"] == "bug_fix"
            assert kwargs["outcome_score"] == 0.9


# ---------------------------------------------------------------------------
# v3.3.0 → v3.3.1 DB migration
# ---------------------------------------------------------------------------


def _make_fake_vec_db(tmp_path, entries):
    """Build a minimal sqlite_vec.db-compatible payload table for tests."""
    import sqlite3

    db = tmp_path / "sqlite_vec.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE payload_dhee ("
        " rowid INTEGER PRIMARY KEY AUTOINCREMENT,"
        " uuid TEXT UNIQUE NOT NULL,"
        " payload TEXT DEFAULT '{}')"
    )
    for i, entry in enumerate(entries):
        con.execute(
            "INSERT INTO payload_dhee (uuid, payload) VALUES (?, ?)",
            (f"uuid-{i}", json.dumps(entry)),
        )
    con.commit()
    con.close()
    return db


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


_SAMPLE_MD = """\
# Project Rules

## Testing

Always run pytest before committing.
Keep tests isolated from external services.

## Auth

JWT tokens use RS256 with 60-min expiry.
Verify on every protected route.

### Secrets

Never commit API keys.
Use environment variables for secrets.

## Coding Style

Follow PEP 8 with 4-space indentation.
"""


class TestChunker:
    def test_empty_input_no_chunks(self):
        assert chunk_markdown("") == []
        assert chunk_markdown("   \n\n  ") == []

    def test_heading_scoped_chunks(self):
        chunks = chunk_markdown(_SAMPLE_MD)
        paths = [c.heading_path for c in chunks]
        assert ("Project Rules", "Testing") in paths
        assert ("Project Rules", "Auth") in paths
        assert ("Project Rules", "Auth", "Secrets") in paths
        assert ("Project Rules", "Coding Style") in paths

    def test_chunk_text_excludes_headings(self):
        chunks = chunk_markdown(_SAMPLE_MD)
        for c in chunks:
            assert not c.text.startswith("#")

    def test_heading_breadcrumb(self):
        chunks = chunk_markdown(_SAMPLE_MD)
        secrets = [c for c in chunks if c.heading_path[-1] == "Secrets"][0]
        assert secrets.heading_breadcrumb == "Project Rules › Auth › Secrets"

    def test_sha_consistent(self):
        a = sha256_of("hello")
        b = sha256_of("hello")
        c = sha256_of("world")
        assert a == b
        assert a != c

    def test_source_path_and_sha_propagated(self):
        chunks = chunk_markdown(_SAMPLE_MD, source_path="CLAUDE.md")
        for c in chunks:
            assert c.source_path == "CLAUDE.md"
            assert c.source_sha
            assert c.source_sha == chunks[0].source_sha

    def test_metadata_shape(self):
        chunks = chunk_markdown(_SAMPLE_MD, source_path="CLAUDE.md")
        meta = chunks[0].to_metadata()
        assert meta["kind"] == "doc_chunk"
        assert meta["source_path"] == "CLAUDE.md"
        assert isinstance(meta["heading_path"], list)
        assert isinstance(meta["chunk_index"], int)

    def test_embedded_text_has_breadcrumb(self):
        chunks = chunk_markdown(_SAMPLE_MD)
        for c in chunks:
            if c.heading_path:
                assert c.to_embedded_text().startswith(c.heading_breadcrumb)

    def test_large_section_split_by_size(self):
        big = "# Big\n\n" + "\n\n".join(f"Para {i}: " + "x " * 200 for i in range(10))
        chunks = chunk_markdown(big, max_chars=500)
        assert len(chunks) > 1
        for c in chunks:
            assert c.heading_path == ("Big",)

    def test_code_fence_not_split(self):
        md = "# Code\n\n```python\n" + "x = 1\n" * 100 + "```\n\nAfter fence."
        chunks = chunk_markdown(md, max_chars=200)
        fenced = [c for c in chunks if "```" in c.text]
        for c in fenced:
            assert c.text.count("```") % 2 == 0  # fences paired


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


class TestAssembler:
    def test_doc_match_source_name(self):
        m = DocMatch(text="x", source_path="/path/to/CLAUDE.md",
                     heading_breadcrumb="Testing", score=0.9, chunk_index=0)
        assert m.source_name == "CLAUDE.md"

    def test_assembled_context_empty_detection(self):
        empty = AssembledContext(doc_matches=[], typed_cognition={})
        assert empty.is_empty
        assert not empty.has_docs
        assert not empty.has_cognition

    def test_assembled_context_with_docs(self):
        ctx = AssembledContext(
            doc_matches=[DocMatch("x", "CLAUDE.md", "Testing", 0.8, 0)],
            typed_cognition={},
        )
        assert ctx.has_docs
        assert not ctx.is_empty

    def test_assembled_context_with_cognition(self):
        ctx = AssembledContext(
            doc_matches=[],
            typed_cognition={"insights": [{"content": "x"}]},
        )
        assert ctx.has_cognition
        assert not ctx.is_empty

    def test_docs_only_with_no_dhee_returns_empty(self):
        """When dhee._engram.search raises, assemble_docs_only returns []."""
        from unittest.mock import MagicMock
        dhee = MagicMock()
        dhee._engram.search.side_effect = Exception("no db")
        result = assemble_docs_only(dhee, "test query")
        assert result == []

    def test_docs_only_empty_query_returns_empty(self):
        from unittest.mock import MagicMock
        dhee = MagicMock()
        assert assemble_docs_only(dhee, "") == []
        assert assemble_docs_only(dhee, "   ") == []

    def test_docs_only_filters_by_kind(self):
        """Only memories with kind=doc_chunk should be returned."""
        from unittest.mock import MagicMock
        dhee = MagicMock()
        dhee._engram.search.return_value = [
            {"memory": "ran: ls", "composite_score": 0.9, "metadata": {"kind": "observation"}},
            {"memory": "Always run tests", "composite_score": 0.8,
             "metadata": {"kind": "doc_chunk", "source_path": "CLAUDE.md",
                         "heading_breadcrumb": "Testing", "chunk_index": 0}},
            {"memory": "ran: git status", "composite_score": 0.7, "metadata": {}},
        ]
        result = assemble_docs_only(dhee, "how to test", score_threshold=0.5)
        assert len(result) == 1
        assert result[0].text == "Always run tests"
        assert result[0].source_path == "CLAUDE.md"

    def test_docs_only_respects_score_threshold(self):
        from unittest.mock import MagicMock
        dhee = MagicMock()
        dhee._engram.search.return_value = [
            {"memory": "low score chunk", "composite_score": 0.3,
             "metadata": {"kind": "doc_chunk", "source_path": "X.md",
                         "heading_breadcrumb": "A", "chunk_index": 0}},
        ]
        result = assemble_docs_only(dhee, "test", score_threshold=0.6)
        assert result == []

    def test_docs_only_respects_token_budget(self):
        from unittest.mock import MagicMock
        dhee = MagicMock()
        dhee._engram.search.return_value = [
            {"memory": "A " * 500, "composite_score": 0.9,
             "metadata": {"kind": "doc_chunk", "source_path": "X.md",
                         "heading_breadcrumb": "Big", "chunk_index": 0}},
            {"memory": "small", "composite_score": 0.8,
             "metadata": {"kind": "doc_chunk", "source_path": "X.md",
                         "heading_breadcrumb": "Small", "chunk_index": 1}},
        ]
        result = assemble_docs_only(dhee, "test", token_budget=50, score_threshold=0.5)
        # Budget is 50 tokens = ~175 chars. First chunk is 1000 chars → skip.
        # Second chunk is 5 chars → fits.
        assert len(result) == 1
        assert result[0].text == "small"


# ---------------------------------------------------------------------------
# Renderer docs section
# ---------------------------------------------------------------------------


class TestRendererDocs:
    def test_docs_render_in_output(self):
        matches = [
            DocMatch("Always run tests", "CLAUDE.md", "Testing", 0.85, 0),
            DocMatch("Use RS256 for JWT", "CLAUDE.md", "Auth", 0.78, 1),
        ]
        xml = render_context({}, doc_matches=matches)
        assert "<r " in xml
        assert "Always run tests" in xml
        assert "Use RS256 for JWT" in xml

    def test_docs_ranked_above_cognition(self):
        """Under tight budget, docs should survive and cognition may be dropped."""
        matches = [DocMatch("Critical rule from CLAUDE.md", "CLAUDE.md", "Rules", 0.9, 0)]
        ctx = {
            "insights": [{"content": "insight one"}],
            "memories": [{"memory": "m " * 200, "score": 0.5}],
        }
        xml = render_context(ctx, max_tokens=120, doc_matches=matches)
        assert "<r " in xml
        assert "Critical rule" in xml

    def test_no_docs_no_section(self):
        xml = render_context({"insights": [{"content": "x"}]}, doc_matches=[])
        assert "<r " not in xml

    def test_none_docs_no_section(self):
        xml = render_context({"insights": [{"content": "x"}]}, doc_matches=None)
        assert "<r " not in xml


# ---------------------------------------------------------------------------
# v3.3.0 → v3.3.1 DB migration
# ---------------------------------------------------------------------------


class TestLegacyPredicates:
    def test_legacy_bash_success_detected(self):
        assert _looks_like_legacy_bash_success({
            "source": "claude_code_hook",
            "tool": "Bash",
            "success": True,
            "text": "ran: ls",
        })

    def test_edit_events_not_legacy_bash(self):
        """File-edit stores are valid in v3.3.1; they must NOT be purged."""
        assert not _looks_like_legacy_bash_success({
            "source": "claude_code_hook",
            "tool": "Edit",
            "success": True,
            "text": "edited /src/auth.py",
        })

    def test_bash_failures_not_legacy(self):
        """Bash failures are valid signal in v3.3.1; they stay."""
        assert not _looks_like_legacy_bash_success({
            "source": "claude_code_hook",
            "tool": "Bash",
            "success": False,
            "text": "bash failed: pytest ...",
        })

    def test_user_entries_not_touched(self):
        """A user-authored memory without the hook source must survive
        even if its text happens to include 'ran:' somewhere."""
        assert not _looks_like_legacy_bash_success({
            "source": "user",
            "text": "I ran: pytest and got a weird result",
        })

    def test_self_referential_text_detected(self):
        assert _looks_like_self_referential({"text": "ran: sqlite3 ~/.dhee/handoff.db 'x'"})
        assert _looks_like_self_referential({"text": "python -m dhee.hooks.claude_code UserPromptSubmit"})

    def test_non_self_ref_text_preserved(self):
        assert not _looks_like_self_referential({"text": "pytest tests/test_auth.py FAILED"})
        assert not _looks_like_self_referential({"text": "edited /src/auth.py"})


class TestPurgeLegacyNoise:
    def test_purge_missing_db_returns_skipped(self, tmp_path):
        result = purge_legacy_noise(tmp_path / "nope.db")
        assert result.skipped_reason == "db_missing"
        assert result.removed == 0

    def test_purge_empty_db_no_ops(self, tmp_path):
        db = _make_fake_vec_db(tmp_path, [])
        result = purge_legacy_noise(db)
        assert result.scanned == 0
        assert result.removed == 0

    def test_purge_removes_legacy_bash_success(self, tmp_path):
        db = _make_fake_vec_db(tmp_path, [
            {"source": "claude_code_hook", "tool": "Bash", "success": True, "text": "ran: ls"},
            {"source": "claude_code_hook", "tool": "Bash", "success": True, "text": "ran: cat foo"},
            {"source": "claude_code_hook", "tool": "Edit", "success": True, "text": "edited /src/a.py"},
        ])
        result = purge_legacy_noise(db)
        assert result.scanned == 3
        assert result.removed == 2

        # Verify the survivor is the edit event.
        import sqlite3
        con = sqlite3.connect(str(db))
        rows = con.execute("SELECT payload FROM payload_dhee").fetchall()
        con.close()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["tool"] == "Edit"

    def test_purge_removes_self_referential(self, tmp_path):
        db = _make_fake_vec_db(tmp_path, [
            {"source": "anything", "text": "ran: sqlite3 ~/.dhee/sqlite_vec.db 'SELECT 1'"},
            {"source": "anything", "text": "ran: python -m dhee.hooks.claude_code Stop"},
            {"source": "anything", "text": "ran: pytest tests/"},
        ])
        result = purge_legacy_noise(db)
        assert result.removed == 2

    def test_purge_is_idempotent(self, tmp_path):
        db = _make_fake_vec_db(tmp_path, [
            {"source": "claude_code_hook", "tool": "Bash", "success": True, "text": "ran: ls"},
        ])
        purge_legacy_noise(db)
        result = purge_legacy_noise(db)
        assert result.removed == 0

    def test_dry_run_reports_count_without_deleting(self, tmp_path):
        db = _make_fake_vec_db(tmp_path, [
            {"source": "claude_code_hook", "tool": "Bash", "success": True, "text": "ran: ls"},
            {"source": "claude_code_hook", "tool": "Bash", "success": True, "text": "ran: pwd"},
        ])
        dry = purge_legacy_noise(db, dry_run=True)
        assert dry.scanned == 2
        assert dry.removed == 2  # reports WHAT WOULD be removed
        import sqlite3
        con = sqlite3.connect(str(db))
        still = con.execute("SELECT COUNT(*) FROM payload_dhee").fetchone()[0]
        con.close()
        assert still == 2  # nothing actually deleted

    def test_purge_preserves_user_authored_entries(self, tmp_path):
        db = _make_fake_vec_db(tmp_path, [
            {"source": "user", "text": "I prefer dark mode"},
            {"source": "user", "text": "ran: marathon last weekend"},
            {"source": "claude_code_hook", "tool": "Bash", "success": True, "text": "ran: ls"},
        ])
        result = purge_legacy_noise(db)
        assert result.removed == 1

    def test_malformed_payload_tolerated(self, tmp_path):
        import sqlite3
        db = tmp_path / "sqlite_vec.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE payload_dhee ("
            " rowid INTEGER PRIMARY KEY AUTOINCREMENT,"
            " uuid TEXT UNIQUE NOT NULL,"
            " payload TEXT DEFAULT '{}')"
        )
        con.execute(
            "INSERT INTO payload_dhee (uuid, payload) VALUES (?, ?)",
            ("u1", "not valid json"),
        )
        con.commit()
        con.close()
        result = purge_legacy_noise(db)
        assert result.scanned == 1
        assert result.removed == 0

    def test_purge_result_dataclass_defaults(self):
        r = PurgeResult()
        assert r.scanned == 0
        assert r.removed == 0
        assert r.db_path is None
        assert r.skipped_reason is None
