"""Claude Code harness adapter.

Binds Claude Code's vendor event vocabulary onto ``CanonicalEvent`` and
delegates each event to the existing handler in ``dhee.hooks.claude_code``.
The runtime dispatch (``python -m dhee.hooks.claude_code <event>``)
continues to live at its historical path so existing ``settings.json``
installs keep working; moving the dispatch module is M7 cleanup.
"""

from __future__ import annotations

from typing import Any, Dict

from dhee.harness.base import CanonicalEvent, HarnessAdapter


_CLAUDE_EVENT_MAP: Dict[str, CanonicalEvent] = {
    "SessionStart": CanonicalEvent.SESSION_START,
    "UserPromptSubmit": CanonicalEvent.USER_PROMPT,
    "PreToolUse": CanonicalEvent.PRE_TOOL,
    "PostToolUse": CanonicalEvent.POST_TOOL,
    "PreCompact": CanonicalEvent.PRE_COMPACT,
    "Stop": CanonicalEvent.SESSION_END,
    "SessionEnd": CanonicalEvent.SESSION_END,
}


class ClaudeCodeAdapter(HarnessAdapter):
    name = "claude_code"
    event_map = _CLAUDE_EVENT_MAP

    def __init__(self) -> None:
        super().__init__()
        # Late import so importing the harness package never triggers
        # Dhee bootstrap (which would be wasteful when a caller just
        # wants the event map).
        from dhee.hooks.claude_code import __main__ as dispatch

        self.register(CanonicalEvent.SESSION_START, dispatch.handle_session_start)
        self.register(CanonicalEvent.USER_PROMPT, dispatch.handle_user_prompt)
        self.register(CanonicalEvent.PRE_TOOL, dispatch.handle_pre_tool)
        self.register(CanonicalEvent.POST_TOOL, dispatch.handle_post_tool)
        self.register(CanonicalEvent.PRE_COMPACT, dispatch.handle_pre_compact)
        self.register(CanonicalEvent.SESSION_END, dispatch.handle_stop)
