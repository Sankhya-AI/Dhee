from __future__ import annotations

from dhee import Client


def test_two_call_memory_injects_preference_on_next_call(tmp_path):
    memory = Client(
        user_id="user_123",
        app_id="elevenlabs:support-agent",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    first_call = memory.run(task="voice support call")
    first_call.before(channel="voice")
    first_call.tool("remember", content="User prefers WhatsApp follow-up.")

    next_call = memory.run(task="voice support call")
    patch = next_call.before(channel="voice")

    assert "WhatsApp" in patch.context
