"""ContextCompactor — summarize long conversation histories."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_COMPACT_PROMPT = """Summarize the following conversation messages into a concise context summary.
Keep key facts, decisions, and action items. Omit pleasantries and repetition.

Messages:
{messages}

Concise summary:"""


class ContextCompactor:
    """Summarize older messages, keep recent ones verbatim.

    Useful for long-running conversations that exceed context windows.
    """

    def __init__(self, llm: Any, max_tokens: int = 4000,
                 keep_recent: int = 5) -> None:
        self._llm = llm
        self._max_tokens = max_tokens
        self._keep_recent = keep_recent

    def should_compact(self, messages: list[dict]) -> bool:
        """Check if compaction is needed based on estimated token count."""
        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = total_chars // 4  # rough estimate
        return estimated_tokens > self._max_tokens

    def compact(self, messages: list[dict]) -> list[dict]:
        """Summarize older messages, keep recent ones verbatim.

        Returns a new message list with a summary message followed by
        the most recent messages.
        """
        if not messages:
            return []

        if len(messages) <= self._keep_recent:
            return messages

        # Split into old (to summarize) and recent (to keep)
        old_messages = messages[:-self._keep_recent]
        recent_messages = messages[-self._keep_recent:]

        # Build text from old messages
        msg_text = "\n".join(
            f"[{m.get('role', 'unknown')}]: {m.get('content', '')}"
            for m in old_messages
        )

        try:
            prompt = _COMPACT_PROMPT.format(messages=msg_text)
            summary = self._llm.generate(prompt)
        except Exception as e:
            logger.warning("Compaction failed: %s. Keeping last %d messages only.", e, self._keep_recent)
            return recent_messages

        # Return summary + recent messages
        summary_message = {
            "role": "system",
            "content": f"[Context summary of {len(old_messages)} earlier messages]\n{summary}",
        }
        return [summary_message] + recent_messages
