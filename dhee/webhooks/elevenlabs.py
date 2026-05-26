"""ElevenLabs webhook normalization helpers."""

from __future__ import annotations

from typing import Any, Optional


def extract_user_and_run(data: dict[str, Any]) -> tuple[str, Optional[str]]:
    initiation = data.get("conversation_initiation_client_data") or {}
    dynamic_vars = initiation.get("dynamic_variables") or {}

    user_id = (
        data.get("user_id")
        or dynamic_vars.get("dhee_user_id")
        or dynamic_vars.get("user_id")
        or data.get("conversation_id")
        or "default"
    )
    run_id = dynamic_vars.get("dhee_run_id")
    return str(user_id), str(run_id) if run_id else None


def transcript_to_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    conversation_id = data.get("conversation_id")

    for turn in data.get("transcript") or []:
        role = turn.get("role")
        message = turn.get("message")
        if not message:
            continue

        if role == "user":
            event_type = "voice.user_transcript"
        elif role in {"agent", "assistant"}:
            event_type = "voice.agent_response"
        else:
            event_type = f"voice.{role or 'unknown'}"

        events.append(
            {
                "type": event_type,
                "content": message,
                "metadata": {
                    "conversation_id": conversation_id,
                    "time_in_call_secs": turn.get("time_in_call_secs"),
                    "tool_calls": turn.get("tool_calls"),
                    "tool_results": turn.get("tool_results"),
                    "feedback": turn.get("feedback"),
                },
            }
        )

    return events


def summary_from_analysis(data: dict[str, Any]) -> str:
    analysis = data.get("analysis") or {}
    return (
        analysis.get("transcript_summary")
        or analysis.get("summary")
        or "ElevenLabs voice call completed."
    )
