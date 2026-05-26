from __future__ import annotations

from dhee.agent_runtime.policy import admit_voice_event, redact_voice_content


def test_voice_policy_admits_durable_preferences():
    decision = admit_voice_event(
        {
            "type": "voice.user_transcript",
            "content": "Please remember I prefer WhatsApp follow-up.",
        }
    )

    assert decision.should_store is True
    assert decision.metadata["channel"] == "voice"
    assert decision.reason == "durable_voice_marker"


def test_voice_policy_rejects_noise_and_secrets():
    noise = admit_voice_event({"type": "voice.user_transcript", "content": "uh yeah okay"})
    secret = admit_voice_event({"type": "voice.user_transcript", "content": "My OTP is 123456"})

    assert noise.should_store is False
    assert noise.reason == "voice_noise"
    assert secret.should_store is False
    assert secret.reason == "sensitive_content"
    assert "[REDACTED]" in redact_voice_content("My OTP is 123456")
