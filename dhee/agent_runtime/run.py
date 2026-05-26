"""Run lifecycle for Dhee's universal agent runtime."""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from dhee.agent_runtime.models import Patch, ToolResult
from dhee.agent_runtime.policy import (
    admit_voice_event,
    contains_voice_secret,
    redact_voice_content,
)
from dhee.agent_runtime.tools import SUPPORTED_ACTIONS, normalize_tool_action


class Run:
    """A single agent interaction backed by Dhee memory."""

    def __init__(
        self,
        plugin: Any,
        user_id: str,
        app_id: str,
        task: Optional[str],
        run_id: Optional[str],
        metadata: dict[str, Any],
    ):
        self.plugin = plugin
        self.user_id = user_id
        self.app_id = app_id
        self.task = task
        self.id = run_id or f"run_{uuid.uuid4().hex}"
        self.metadata = metadata
        self.events: list[dict[str, Any]] = []

    def before(
        self,
        input: Optional[str] = None,
        budget_tokens: int = 900,
        channel: str = "generic",
    ) -> Patch:
        raw_context = self.plugin.session_start(
            task_description=self.task or input or "agent session",
            user_id=self.user_id,
        )
        context = render_agent_context(
            raw_context,
            channel=channel,
            budget_tokens=budget_tokens,
        )
        return Patch(
            run_id=self.id,
            user_id=self.user_id,
            app_id=self.app_id,
            context=context,
            dynamic_variables={
                "dhee_context": context,
                "dhee_run_id": self.id,
                "dhee_user_id": self.user_id,
                "dhee_app_id": self.app_id,
            },
            metadata={
                "task": self.task,
                "channel": channel,
                **self.metadata,
            },
        )

    def event(
        self,
        event_type: str,
        content: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        event = {
            "type": event_type,
            "content": content,
            "metadata": metadata or {},
        }
        self.events.append(event)

        admitted = admit_voice_event(event)
        if admitted.should_store:
            self.plugin.remember(
                admitted.content,
                user_id=self.user_id,
                metadata={
                    "source": "dhee_agent_runtime",
                    "app_id": self.app_id,
                    "run_id": self.id,
                    "event_type": event_type,
                    **admitted.metadata,
                },
            )

        return {
            "ok": True,
            "admitted": admitted.should_store,
            "reason": admitted.reason,
        }

    def tool(
        self,
        action: str,
        query: Optional[str] = None,
        content: Optional[str] = None,
        summary: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ToolResult:
        action = normalize_tool_action(action)
        metadata = metadata or {}

        if action not in SUPPORTED_ACTIONS:
            return ToolResult(
                ok=False,
                speakable_summary="Unknown Dhee memory action.",
                result={"error": f"Unknown action: {action}"},
            )

        if action == "recall":
            results = self.plugin.recall(
                query=query or "",
                user_id=self.user_id,
            )
            return ToolResult(
                speakable_summary=voice_summarize_recall(results),
                result={
                    "memories": results,
                    "confidence": "high" if results else "none",
                },
            )

        text = content or summary or ""
        if action in {"remember", "correct", "checkpoint"} and contains_voice_secret(text):
            return ToolResult(
                ok=False,
                speakable_summary="I can't save sensitive details like codes, passwords, tokens, or payment numbers.",
                result={"error": "sensitive_content"},
            )

        if action == "remember":
            clean_content = redact_voice_content(content or "").strip()
            if not clean_content:
                return empty_write_result("remember")
            result = self.plugin.remember(
                clean_content,
                user_id=self.user_id,
                metadata={
                    "source": "voice_tool",
                    "app_id": self.app_id,
                    "run_id": self.id,
                    **metadata,
                },
            )
            return ToolResult(
                speakable_summary="I saved that.",
                result=result,
            )

        if action == "checkpoint":
            checkpoint_summary = (summary or content or "").strip()
            if not checkpoint_summary:
                return empty_write_result("checkpoint")
            result = self.plugin.checkpoint(
                summary=checkpoint_summary,
                user_id=self.user_id,
                status="active",
            )
            return ToolResult(
                speakable_summary="Checkpoint saved.",
                result=result,
            )

        clean_content = redact_voice_content(content or "").strip()
        if not clean_content:
            return empty_write_result("correct")
        clean = normalize_voice_correction(clean_content)
        result = self.plugin.remember(
            clean,
            user_id=self.user_id,
            metadata={
                "source": "voice_correction",
                "app_id": self.app_id,
                "run_id": self.id,
                "retention_policy": "durable",
                "channel": "voice",
                **metadata,
            },
        )
        return ToolResult(
            speakable_summary="I updated that.",
            result=result,
        )

    def finish(
        self,
        outcome: str = "completed",
        summary: Optional[str] = None,
        outcome_score: Optional[float] = None,
        what_worked: Optional[str] = None,
        what_failed: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        finish_summary = render_finish_summary(
            summary or self._fallback_summary(outcome),
            app_id=self.app_id,
            run_id=self.id,
            outcome=outcome,
            events_observed=len(self.events),
            metadata=metadata or {},
        )
        result = self.plugin.session_end(
            summary=finish_summary,
            user_id=self.user_id,
            status=outcome,
            outcome_score=outcome_score,
            what_worked=what_worked,
            what_failed=what_failed,
        )
        result.setdefault("metadata", {})
        result["metadata"].update(
            {
                "app_id": self.app_id,
                "run_id": self.id,
                "events_observed": len(self.events),
                **(metadata or {}),
            }
        )
        return result

    def _fallback_summary(self, outcome: str) -> str:
        return f"Agent run {self.id} ended with outcome={outcome}. Events observed={len(self.events)}."


def render_agent_context(raw_context: Any, channel: str, budget_tokens: int) -> str:
    """Render a bounded context block for external agents."""

    header = "## Dhee Memory Context\n\nUse this only when relevant. Do not read it aloud."
    raw_text = raw_context if isinstance(raw_context, str) else str(raw_context)
    channel_line = f"\n\nChannel: {channel}" if channel and channel != "generic" else ""
    max_chars = max(1, int(budget_tokens)) * 4
    return f"{header}{channel_line}\n\n{raw_text}"[:max_chars]


def voice_summarize_recall(results: Any) -> str:
    """Keep recall responses short enough for voice agents."""

    if not results:
        return "I could not find a relevant previous memory."
    if isinstance(results, list):
        memories = []
        for item in results[:3]:
            if isinstance(item, dict):
                memories.append(str(item.get("memory") or item.get("content") or item))
            else:
                memories.append(str(item))
        text = " ".join(memories)
    else:
        text = str(results)
    return text[:700]


def empty_write_result(action: str) -> ToolResult:
    return ToolResult(
        ok=False,
        speakable_summary="I need something specific to save.",
        result={"error": "empty_content", "action": action},
    )


def render_finish_summary(
    summary: str,
    app_id: str,
    run_id: str,
    outcome: str,
    events_observed: int,
    metadata: dict[str, Any],
) -> str:
    base = (summary or "").strip()
    compact_metadata = {
        "app_id": app_id,
        "run_id": run_id,
        "outcome": outcome,
        "events_observed": events_observed,
        **{
            key: value
            for key, value in metadata.items()
            if value not in (None, "", [], {})
        },
    }
    return f"{base}\n\nAgent runtime metadata: {compact_metadata}"


_PREFERRED_NAME_RE = re.compile(
    r"(?i)\b(?:preferred\s+name|name)\s+(?:is|to|as)\s+([A-Za-z][A-Za-z'_-]*)\b"
)
_PREFERRED_NAME_FROM_TO_RE = re.compile(
    r"(?i)\b(?:preferred\s+name|name)\s+from\s+.+?\s+to\s+([A-Za-z][A-Za-z'_-]*)\b"
)


def normalize_voice_correction(content: str) -> str:
    """Store corrections as current-truth instructions for future context."""

    clean = (content or "").strip()
    if not clean:
        return "Current correction from user: unspecified correction. Treat older conflicting memories as outdated."

    for pattern in (_PREFERRED_NAME_FROM_TO_RE, _PREFERRED_NAME_RE):
        match = pattern.search(clean)
        if match:
            name = match.group(1).strip(" .,!?:;")
            return (
                f"Current correction: user's preferred name is {name}. "
                "Treat older conflicting name memories as outdated."
            )

    return (
        f"Current correction from user: {clean}. "
        "Treat older conflicting memories as outdated."
    )
