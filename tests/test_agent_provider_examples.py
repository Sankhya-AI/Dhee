from __future__ import annotations

import sys
import types

import dhee
from dhee.providers.elevenlabs import ElevenAgent, ElevenLabsAgent


def test_elevenlabs_native_provider_exposes_full_dhee_flow(tmp_path):
    provider = ElevenLabsAgent(
        public_base_url="https://memory.example.com/",
        user_id="user_123",
        agent_id="support-agent",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    call_start = provider.start_call()
    tool_schema = provider.server_tool_schema()
    stored = provider.handle_memory_tool(
        {
            "action": "remember",
            "content": "User prefers WhatsApp follow-up.",
            "conversation_id": "conv_123",
        }
    )
    recalled = provider.handle_memory_tool(
        {
            "action": "recall",
            "query": "follow-up preference",
        }
    )

    assert call_start["dynamic_variables"]["dhee_user_id"] == "user_123"
    assert call_start["dynamic_variables"]["dhee_app_id"] == "elevenlabs:support-agent"
    assert "{{dhee_context}}" in provider.prompt_snippet()
    assert tool_schema["url"] == "https://memory.example.com/v1/tools/dhee_memory"
    assert tool_schema["body"]["app_id"] == "{{dhee_app_id}}"
    assert provider.post_call_webhook_url() == "https://memory.example.com/v1/webhooks/elevenlabs/post_call"
    assert stored["ok"] is True
    assert "WhatsApp" in recalled["speakable_summary"]


def test_elevenlabs_native_provider_is_exported_from_root():
    assert dhee.ElevenLabsAgent is ElevenLabsAgent
    assert dhee.ElevenAgent is ElevenAgent


def test_elevenlabs_native_provider_checkpoints_post_call(tmp_path):
    provider = ElevenLabsAgent(
        public_base_url="https://memory.example.com",
        user_id="user_123",
        agent_id="support-agent",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )
    provider.start_call()

    result = provider.checkpoint_post_call(
        {
            "type": "post_call_transcription",
            "data": {
                "conversation_id": "conv_123",
                "agent_id": "support-agent",
                "transcript": [
                    {
                        "role": "user",
                        "message": "Please remember I prefer WhatsApp follow-up.",
                    }
                ],
                "analysis": {
                    "transcript_summary": "User asked for WhatsApp follow-up.",
                    "call_successful": True,
                },
                "status": "done",
            },
        }
    )

    assert result["session_saved"] is True
    assert result["metadata"]["provider"] == "elevenlabs"
    assert result["metadata"]["conversation_id"] == "conv_123"


def test_elevenlabs_native_sdk_bridge_uses_dhee_context_and_tools(tmp_path, monkeypatch):
    installed = install_fake_elevenlabs_sdk(monkeypatch)
    provider = ElevenAgent(
        public_base_url="https://memory.example.com",
        user_id="user_123",
        agent_id="support-agent",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    initiation = provider.conversation_initiation_data()
    tools = provider.client_tools()
    tool_result = tools.registered["dhee_memory"]["fn"](
        {"action": "remember", "content": "User prefers WhatsApp follow-up."}
    )
    audio_interface = object()
    conversation = provider.create_conversation(
        api_key="test-key",
        audio_interface=audio_interface,
    )
    conversation.kwargs["callback_user_transcript"]("Please remember WhatsApp.")
    conversation.kwargs["callback_agent_response"]("I saved that.")
    started = provider.start_session(api_key="test-key", audio_interface=audio_interface)
    observed = []
    provider.wrap_user_transcript(lambda transcript: observed.append(transcript))(
        "Please use WhatsApp."
    )

    assert initiation.dynamic_variables["dhee_user_id"] == "user_123"
    assert initiation.dynamic_variables["dhee_app_id"] == "elevenlabs:support-agent"
    assert tool_result["ok"] is True
    assert installed["client"].api_key == "test-key"
    assert conversation.kwargs["agent_id"] == "support-agent"
    assert conversation.kwargs["config"].dynamic_variables["dhee_run_id"]
    assert conversation.kwargs["audio_interface"] is audio_interface
    assert conversation.kwargs["client_tools"].registered["dhee_memory"]["is_async"] is False
    assert started.started is True
    assert observed == ["Please use WhatsApp."]


def test_elevenlabs_native_provider_does_not_define_forbidden_client_wrappers():
    source = "\n".join(
        path.read_text()
        for path in [
            __import__("pathlib").Path("dhee/providers/elevenlabs.py"),
            __import__("pathlib").Path("dhee/providers/base.py"),
        ]
    )

    assert "DheeElevenLabsClient" not in source
    assert "ElevenLabsClient" not in source
    assert "GeminiClient" not in source
    assert "OpenAIClient" not in source


def install_fake_elevenlabs_sdk(monkeypatch):
    installed = {}

    elevenlabs_pkg = types.ModuleType("elevenlabs")
    client_mod = types.ModuleType("elevenlabs.client")
    conversational_pkg = types.ModuleType("elevenlabs.conversational_ai")
    conversation_mod = types.ModuleType("elevenlabs.conversational_ai.conversation")
    audio_mod = types.ModuleType("elevenlabs.conversational_ai.default_audio_interface")

    class FakeElevenLabs:
        def __init__(self, api_key=None):
            self.api_key = api_key
            installed["client"] = self

    class FakeConversationInitiationData:
        def __init__(self, dynamic_variables):
            self.dynamic_variables = dynamic_variables

    class FakeClientTools:
        def __init__(self):
            self.registered = {}

        def register(self, name, fn, is_async=False):
            self.registered[name] = {"fn": fn, "is_async": is_async}

    class FakeDefaultAudioInterface:
        pass

    class FakeConversation:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False

        def start_session(self):
            self.started = True

    client_mod.ElevenLabs = FakeElevenLabs
    conversation_mod.ClientTools = FakeClientTools
    conversation_mod.Conversation = FakeConversation
    conversation_mod.ConversationInitiationData = FakeConversationInitiationData
    audio_mod.DefaultAudioInterface = FakeDefaultAudioInterface

    monkeypatch.setitem(sys.modules, "elevenlabs", elevenlabs_pkg)
    monkeypatch.setitem(sys.modules, "elevenlabs.client", client_mod)
    monkeypatch.setitem(sys.modules, "elevenlabs.conversational_ai", conversational_pkg)
    monkeypatch.setitem(
        sys.modules,
        "elevenlabs.conversational_ai.conversation",
        conversation_mod,
    )
    monkeypatch.setitem(
        sys.modules,
        "elevenlabs.conversational_ai.default_audio_interface",
        audio_mod,
    )

    return installed
