"""Tool-action helpers for Dhee's small external memory surface."""

from __future__ import annotations


MEMORY_TOOL_NAME = "dhee_memory"
SUPPORTED_ACTIONS = {"recall", "remember", "checkpoint", "correct"}


def normalize_tool_action(action: str) -> str:
    return str(action or "").lower().strip()
