"""Tests for the Dhee Claude Code hook system.

Covers: XML renderer, privacy filter, installer, dispatch handlers.
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
    HEADER,
    estimate_tokens,
    render_context,
)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _extract_xml(rendered: str) -> ET.Element:
    """Extract and parse the <dhee-context> XML block from rendered output."""
    match = re.search(r"<dhee-context[\s>]", rendered)
    if not match:
        raise ValueError("No <dhee-context> block found")
    return ET.fromstring(rendered[match.start() :])


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
    def test_empty_context_produces_minimal_xml(self):
        xml = render_context({})
        assert xml.startswith(HEADER)
        assert "<dhee-context>" in xml
        assert "</dhee-context>" in xml

    def test_empty_context_has_no_sections(self):
        xml = render_context({})
        assert "<session" not in xml
        assert "<memories" not in xml
        assert "<insights" not in xml

    def test_rich_context_produces_all_sections(self):
        xml = render_context(_rich_ctx())
        for tag in ["session", "performance", "insights", "intentions", "memories", "beliefs", "policies", "warnings"]:
            assert f"<{tag}" in xml, f"Missing section: {tag}"

    def test_task_description_in_root_attribute(self):
        xml = render_context({}, task_description="fix auth")
        assert 'task="fix auth"' in xml

    def test_memories_sorted_by_score_descending(self):
        ctx = {
            "memories": [
                {"memory": "low", "score": 0.1},
                {"memory": "high", "score": 0.9},
                {"memory": "mid", "score": 0.5},
            ]
        }
        xml = render_context(ctx)
        root = _extract_xml(xml)
        scores = [float(m.get("s")) for m in root.findall("memories/m")]
        assert scores == sorted(scores, reverse=True)

    def test_xml_injection_escaped(self):
        ctx = {
            "memories": [{"memory": '<script>alert("xss")</script> & co', "score": 0.5}],
            "warnings": ['contains "quoted" <text>'],
        }
        xml = render_context(ctx)
        assert "<script>" not in xml
        root = _extract_xml(xml)
        mem = root.find("memories/m")
        assert mem is not None
        assert '<script>alert("xss")</script>' in mem.text

    def test_budget_drops_low_priority_sections(self):
        ctx = _rich_ctx()
        ctx["memories"] = [{"memory": f"mem {i} " * 20, "score": 0.5} for i in range(20)]
        tight = render_context(ctx, max_tokens=200)
        full = render_context(ctx, max_tokens=3000)
        assert estimate_tokens(tight) < estimate_tokens(full)
        assert "<session" in tight
        assert "<policies" not in tight

    def test_session_block_has_highest_priority(self):
        ctx = _rich_ctx()
        ctx["memories"] = [{"memory": f"m{i} " * 50, "score": 0.5} for i in range(50)]
        tight = render_context(ctx, max_tokens=150)
        assert "<session" in tight

    def test_empty_sections_not_emitted(self):
        ctx = {"memories": [], "insights": [], "warnings": []}
        xml = render_context(ctx)
        assert "<memories" not in xml
        assert "<insights" not in xml
        assert "<warnings" not in xml

    def test_estimate_tokens_monotone(self):
        assert estimate_tokens("abc") <= estimate_tokens("abc" * 100)

    def test_intention_triggers_attribute(self):
        ctx = {"intentions": [{"content": "use freezegun", "trigger_keywords": ["flaky", "time"]}]}
        xml = render_context(ctx)
        root = _extract_xml(xml)
        intent = root.find("intentions/i")
        assert intent is not None
        assert intent.get("triggers") == "flaky,time"
        assert intent.text == "use freezegun"

    def test_performance_row_attributes(self):
        ctx = {"performance": [{"task_type": "bug_fix", "total_attempts": 3, "best_score": 0.9, "avg_score": 0.82, "trend": 0.05}]}
        xml = render_context(ctx)
        root = _extract_xml(xml)
        row = root.find("performance/row")
        assert row is not None
        assert row.get("type") == "bug_fix"
        assert row.get("attempts") == "3"

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
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other-tool"}]}]},
            "permissions": {"allow": ["Read"]},
        }))
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            settings = json.loads(fake.read_text())
            assert "permissions" in settings
            usp = settings["hooks"]["UserPromptSubmit"]
            assert len(usp) == 2
            cmds = [e["hooks"][0]["command"] for e in usp]
            assert "other-tool" in cmds

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
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other-tool"}]}]},
        }))
        with patch("dhee.hooks.claude_code.install._settings_path", return_value=fake):
            install_hooks()
            uninstall_hooks()
            settings = json.loads(fake.read_text())
            usp = settings["hooks"]["UserPromptSubmit"]
            assert len(usp) == 1
            assert usp[0]["hooks"][0]["command"] == "other-tool"

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


# ---------------------------------------------------------------------------
# Dispatch handlers (unit-level, no real Dhee)
# ---------------------------------------------------------------------------


class TestDispatchHandlers:
    def test_post_tool_edit_builds_content(self):
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
            assert "/src/auth.py" in call_args.kwargs.get("content", call_args[1].get("content", ""))

    def test_post_tool_ignores_read_tools(self):
        from dhee.hooks.claude_code.__main__ import handle_post_tool

        result = handle_post_tool({"tool_name": "Read", "tool_input": {}, "success": True})
        assert result == {}

    def test_post_tool_filters_secrets(self):
        from dhee.hooks.claude_code.__main__ import handle_post_tool

        with patch("dhee.hooks.claude_code.__main__._get_dhee") as mock:
            mock.return_value.remember.return_value = {"stored": True}
            handle_post_tool({
                "tool_name": "Bash",
                "tool_input": {"command": "echo sk-ant-api03-secret12345678901234567890"},
                "success": True,
            })
            if mock.return_value.remember.called:
                content = mock.return_value.remember.call_args.kwargs.get(
                    "content", mock.return_value.remember.call_args[1].get("content", "")
                )
                assert "sk-ant-api03" not in content

    def test_user_prompt_empty_returns_empty(self):
        from dhee.hooks.claude_code.__main__ import handle_user_prompt

        result = handle_user_prompt({"prompt": ""})
        assert result == {}

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
