from __future__ import annotations

from dhee import Client


def test_agent_runtime_tool_remember_and_recall_are_voice_friendly(tmp_path):
    client = Client(
        user_id="user_123",
        app_id="elevenlabs:support-agent",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )
    run = client.run(task="voice support call")

    stored = run.tool("remember", content="User prefers WhatsApp follow-up.")
    recalled = run.tool("recall", query="follow-up preference")

    assert stored.ok is True
    assert stored.speakable_summary == "I saved that."
    assert recalled.ok is True
    assert "WhatsApp" in recalled.speakable_summary
    assert "memories" in recalled.result


def test_agent_runtime_tool_refuses_secret_storage(tmp_path):
    client = Client(
        user_id="user_123",
        app_id="elevenlabs:support-agent",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )
    run = client.run(task="voice support call")

    result = run.tool("remember", content="My password is hunter2.")

    assert result.ok is False
    assert result.result["error"] == "sensitive_content"


def test_agent_runtime_tool_rejects_empty_writes(tmp_path):
    client = Client(
        user_id="user_123",
        app_id="elevenlabs:support-agent",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )
    run = client.run(task="voice support call")

    for action in ("remember", "correct", "checkpoint"):
        result = run.tool(action, content="   ")
        assert result.ok is False
        assert result.result["error"] == "empty_content"
        assert result.result["action"] == action
