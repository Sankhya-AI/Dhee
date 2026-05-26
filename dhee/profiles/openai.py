"""OpenAI profile for Dhee's universal runtime.

This module emits prompt/tool configuration only. It intentionally does not
create or wrap an OpenAI SDK client.
"""

from __future__ import annotations


def prompt_snippet(variable_name: str = "dhee_context") -> str:
    return f"""
Use the Dhee memory context only when relevant:
{{{{{variable_name}}}}}

Do not quote memory internals. Use it to avoid repeated questions, continue open loops, and honor durable user preferences.
""".strip()


def tool_schema() -> dict:
    """Return the Chat Completions-style tool schema."""

    return {
        "type": "function",
        "function": {
            "name": "dhee_memory",
            "description": "Recall, store, correct, or checkpoint durable user memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["recall", "remember", "correct", "checkpoint"]},
                    "query": {"type": "string"},
                    "content": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    }


def responses_tool_schema(strict: bool = True) -> dict:
    """Return the Responses API-style function tool schema."""

    return {
        "type": "function",
        "name": "dhee_memory",
        "description": "Recall, store, correct, or checkpoint durable user memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["recall", "remember", "correct", "checkpoint"],
                },
                "query": {"type": "string"},
                "content": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        "strict": strict,
    }
