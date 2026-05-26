from __future__ import annotations

from dhee.agent_runtime.models import Patch
from dhee.profiles import elevenlabs


def test_elevenlabs_profile_generates_dynamic_variables_and_tool_schema():
    patch = Patch(
        run_id="run_abc",
        user_id="user_123",
        app_id="elevenlabs:support-agent",
        context="memory block",
    )

    variables = elevenlabs.dynamic_variables_from_patch(patch)
    schema = elevenlabs.server_tool_schema("https://memory.example.com/")

    assert variables == {
        "dhee_context": "memory block",
        "dhee_run_id": "run_abc",
        "dhee_user_id": "user_123",
        "dhee_app_id": "elevenlabs:support-agent",
    }
    assert schema["name"] == "dhee_memory"
    assert schema["url"] == "https://memory.example.com/v1/tools/dhee_memory"
    assert schema["headers"]["Authorization"] == "Bearer {{secret__dhee_token}}"
    assert schema["body"]["app_id"] == "{{dhee_app_id}}"
    assert "{{system__conversation_history}}" == schema["body"]["conversation_history"]
    assert "{{dhee_context}}" in elevenlabs.prompt_snippet()
