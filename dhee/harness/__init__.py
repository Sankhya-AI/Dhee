"""Harness adapter layer — canonical event vocabulary for Dhee.

Dhee is harness-agnostic memory + cognition. Real CLIs (Claude Code,
Codex) have their own event names (``PreToolUse`` vs ``pre_tool_use``,
``SessionEnd`` vs ``session_end``, ...). The ``harness`` layer is the
single place where vendor vocabularies are translated into Dhee's
canonical events, so the core stays uncoupled from any one harness.

Usage::

    from dhee.harness import CanonicalEvent, get_adapter

    adapter = get_adapter("claude_code")
    result = adapter.dispatch("PreToolUse", payload)  # vendor event
    # or
    adapter.handle(CanonicalEvent.PRE_TOOL, payload)   # canonical event

The stdin-driven runtime command for Claude Code still ships at
``python -m dhee.hooks.claude_code <event>`` to avoid breaking existing
``~/.claude/settings.json`` installs; relocating that dispatch module
into ``dhee/harness/`` is a separate M7 cleanup.
"""

from dhee.harness.base import CanonicalEvent, HarnessAdapter, get_adapter
from dhee.harness.install import disable_harnesses, harness_status, install_harnesses

__all__ = [
    "CanonicalEvent",
    "HarnessAdapter",
    "get_adapter",
    "install_harnesses",
    "disable_harnesses",
    "harness_status",
]
