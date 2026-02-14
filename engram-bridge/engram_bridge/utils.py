"""Utilities for message formatting and splitting."""

from __future__ import annotations

TELEGRAM_MAX_LENGTH = 4096


def split_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split text at newline boundaries to fit Telegram's message limit."""
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > max_length:
            if current:
                chunks.append(current)
            # If a single line exceeds max, hard-split it
            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text[:max_length]]


def format_tool_use(tool_name: str, args: dict | None = None) -> str:
    """Format a tool call for display: [Reading auth.py]"""
    if not args:
        return f"[Using {tool_name}]"
    # Show the most relevant arg
    for key in ("file_path", "path", "command", "pattern", "query"):
        if key in args:
            val = str(args[key])
            if len(val) > 60:
                val = val[:57] + "..."
            return f"[{tool_name}: {val}]"
    return f"[Using {tool_name}]"
