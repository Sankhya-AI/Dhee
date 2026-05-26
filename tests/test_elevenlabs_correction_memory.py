from __future__ import annotations

from dhee import Client
from dhee.agent_runtime.run import normalize_voice_correction


def test_correction_normalizer_extracts_current_preferred_name():
    correction = normalize_voice_correction("User corrected preferred name from Neil to Neel.")

    assert correction == (
        "Current correction: user's preferred name is Neel. "
        "Treat older conflicting name memories as outdated."
    )


def test_voice_correction_is_injected_as_current_truth_on_next_call(tmp_path):
    memory = Client(
        user_id="user_123",
        app_id="elevenlabs:support-agent",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    first_call = memory.run(task="voice support call")
    first_call.tool("remember", content="User's preferred name is Neil.")

    second_call = memory.run(task="voice support call")
    corrected = second_call.tool(
        "correct",
        content="User corrected preferred name from Neil to Neel.",
    )

    third_call = memory.run(task="voice support call")
    patch = third_call.before(channel="voice")

    assert corrected.ok is True
    assert "Current correction: user's preferred name is Neel" in patch.context
    assert "older conflicting name memories as outdated" in patch.context
