"""M6.1 regression — harness adapter base + Claude Code / Codex adapters.

Plan reference: encapsulated-rolling-bengio.md, Movement 6.

These tests lock in the canonical event contract. If a future harness
(or a new CLI version) changes its vendor event name, only the
``event_map`` needs to be touched — the core keeps seeing the same
``CanonicalEvent`` values.
"""

from __future__ import annotations

import pytest

from dhee.harness import CanonicalEvent, HarnessAdapter, get_adapter
from dhee.harness.base import known_adapters
from dhee.harness.claude_code import _CLAUDE_EVENT_MAP, ClaudeCodeAdapter
from dhee.harness.codex import _CODEX_EVENT_MAP, CodexAdapter


class TestCanonicalEvent:
    def test_values_are_snake_case(self):
        for e in CanonicalEvent:
            assert e.value == e.value.lower()
            assert " " not in e.value

    def test_covers_every_lifecycle_stage(self):
        # The canonical vocabulary is deliberately small. If we grow it,
        # this test flags the change explicitly.
        assert {e.value for e in CanonicalEvent} == {
            "session_start",
            "user_prompt",
            "pre_tool",
            "post_tool",
            "pre_compact",
            "session_end",
        }


class TestRegistry:
    def test_claude_code_registered(self):
        assert "claude_code" in known_adapters()
        adapter = get_adapter("claude_code")
        assert isinstance(adapter, ClaudeCodeAdapter)

    def test_codex_registered(self):
        assert "codex" in known_adapters()
        adapter = get_adapter("codex")
        assert isinstance(adapter, CodexAdapter)

    def test_unknown_adapter_raises(self):
        with pytest.raises(KeyError):
            get_adapter("nonexistent")


class TestClaudeCodeAdapter:
    def test_event_map_covers_all_claude_events(self):
        # Lock the mapping so a future rename on either side blows this up.
        assert _CLAUDE_EVENT_MAP == {
            "SessionStart": CanonicalEvent.SESSION_START,
            "UserPromptSubmit": CanonicalEvent.USER_PROMPT,
            "PreToolUse": CanonicalEvent.PRE_TOOL,
            "PostToolUse": CanonicalEvent.POST_TOOL,
            "PreCompact": CanonicalEvent.PRE_COMPACT,
            "Stop": CanonicalEvent.SESSION_END,
            "SessionEnd": CanonicalEvent.SESSION_END,
        }

    def test_translate_unknown_returns_none(self):
        adapter = ClaudeCodeAdapter()
        assert adapter.translate("NotARealEvent") is None

    def test_dispatch_unknown_returns_empty_dict(self):
        adapter = ClaudeCodeAdapter()
        assert adapter.dispatch("NotARealEvent", {}) == {}

    def test_handlers_registered_for_every_canonical_event(self):
        adapter = ClaudeCodeAdapter()
        for ev in CanonicalEvent:
            assert ev in adapter._handlers, f"missing handler for {ev}"


class TestCodexAdapter:
    def test_event_map_is_session_end_only(self):
        assert _CODEX_EVENT_MAP == {
            "session_end": CanonicalEvent.SESSION_END,
            "SessionEnd": CanonicalEvent.SESSION_END,
        }

    def test_no_transcript_returns_status(self, tmp_path):
        adapter = CodexAdapter()
        # Point at an empty directory so find_latest_codex_log returns None.
        result = adapter.dispatch(
            "session_end",
            {"sessions_root": str(tmp_path / "no_such_dir")},
        )
        assert result.get("status") == "no_log"


class TestHandlerErrorIsolation:
    def test_handler_exception_returns_empty_dict(self):
        class Broken(HarnessAdapter):
            name = "broken"
            event_map = {"Evt": CanonicalEvent.PRE_TOOL}

        a = Broken()

        def boom(_payload):
            raise RuntimeError("boom")

        a.register(CanonicalEvent.PRE_TOOL, boom)
        # Adapter MUST swallow handler errors so the host CLI isn't broken.
        assert a.dispatch("Evt", {}) == {}
