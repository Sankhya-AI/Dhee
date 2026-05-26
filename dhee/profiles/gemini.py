"""Gemini profile for Dhee's universal runtime.

This module emits prompt/tool configuration only. It intentionally does not
create or wrap a Gemini SDK client.
"""

from __future__ import annotations


def prompt_snippet(variable_name: str = "dhee_context") -> str:
    return f"""
Use the Dhee memory context only when relevant:
{{{{{variable_name}}}}}

Do not read the memory block aloud. Use it to preserve continuity, preferences, and unresolved tasks.
""".strip()


def function_declaration() -> dict:
    return {
        "name": "dhee_memory",
        "description": "Recall, store, correct, or checkpoint durable user memory.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["recall", "remember", "correct", "checkpoint"],
                },
                "query": {"type": "STRING"},
                "content": {"type": "STRING"},
                "summary": {"type": "STRING"},
            },
            "required": ["action"],
        },
    }
