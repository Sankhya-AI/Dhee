"""ElevenLabs profile for Dhee's universal agent runtime."""

from __future__ import annotations

from typing import Any


def prompt_snippet() -> str:
    return """
## Dhee Memory Context

Use this memory context only when relevant:
{{dhee_context}}

Rules:
- Do not read the memory context aloud.
- Do not mention Dhee unless the user asks.
- Use it naturally to avoid making the user repeat themselves.
- Call the dhee_memory tool when the user asks about previous calls, preferences, unresolved tasks, or corrections.
- Treat explicit corrections in memory as the current truth when older memories conflict.
- Never store passwords, OTPs, payment details, API keys, or secrets.
""".strip()


def dynamic_variables_from_patch(patch: Any) -> dict[str, Any]:
    return {
        "dhee_context": patch.context,
        "dhee_run_id": patch.run_id,
        "dhee_user_id": patch.user_id,
        "dhee_app_id": patch.app_id,
    }


def server_tool_schema(public_base_url: str) -> dict[str, Any]:
    return {
        "name": "dhee_memory",
        "description": (
            "Recall, store, correct, or checkpoint durable user memory. "
            "Use only for previous calls, user preferences, unresolved tasks, corrections, and follow-up commitments."
        ),
        "method": "POST",
        "url": f"{public_base_url.rstrip('/')}/v1/tools/dhee_memory",
        "headers": {
            "Authorization": "Bearer {{secret__dhee_token}}",
        },
        "body": {
            "user_id": "{{dhee_user_id}}",
            "app_id": "{{dhee_app_id}}",
            "run_id": "{{dhee_run_id}}",
            "conversation_id": "{{system__conversation_id}}",
            "action": "{{action}}",
            "query": "{{query}}",
            "content": "{{content}}",
            "summary": "{{summary}}",
            "conversation_history": "{{system__conversation_history}}",
        },
    }


def init_instructions(public_url: str) -> str:
    tool = server_tool_schema(public_url)
    return f"""
1. Add dynamic variables:
   dhee_context
   dhee_run_id
   dhee_user_id
   dhee_app_id
   secret__dhee_token

2. Add this to your ElevenLabs system prompt:

{prompt_snippet()}

3. Add server tool:
   POST {tool["url"]}

4. Add post-call webhook:
   POST {public_url.rstrip('/')}/v1/webhooks/elevenlabs/post_call

5. Set webhook secret:
   ELEVENLABS_WEBHOOK_SECRET=...
""".strip()
