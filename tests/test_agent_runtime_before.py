from __future__ import annotations

from dhee import Client, Patch


def test_agent_runtime_before_returns_context_patch(tmp_path):
    client = Client(
        user_id="user_123",
        app_id="elevenlabs:support-agent",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    patch = client.run(task="voice support call").before(channel="voice")

    assert isinstance(patch, Patch)
    assert patch.run_id.startswith("run_")
    assert patch.dynamic_variables["dhee_context"] == patch.context
    assert patch.dynamic_variables["dhee_run_id"] == patch.run_id
    assert patch.dynamic_variables["dhee_user_id"] == "user_123"
    assert patch.dynamic_variables["dhee_app_id"] == "elevenlabs:support-agent"
    assert "Do not read it aloud" in patch.context
