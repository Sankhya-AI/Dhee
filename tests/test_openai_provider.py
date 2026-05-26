from __future__ import annotations

import json
import sys
import types

import dhee
from dhee.providers.openai import OpenAIAgent, OpenAIResponsesAgent


def test_openai_native_provider_exposes_dhee_runtime(tmp_path):
    agent = OpenAIAgent(
        user_id="user_123",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    started = agent.start()
    stored = agent.handle_tool_call(
        {
            "name": "dhee_memory",
            "arguments": json.dumps(
                {"action": "remember", "content": "User prefers short answers."}
            ),
        }
    )
    recalled = agent.handle_tool_call(
        {
            "name": "dhee_memory",
            "arguments": {"action": "recall", "query": "answer preference"},
        }
    )

    assert started["dynamic_variables"]["dhee_user_id"] == "user_123"
    assert started["dynamic_variables"]["dhee_app_id"] == "openai:agent"
    assert agent.tool_schema()["name"] == "dhee_memory"
    assert agent.tool_schema()["strict"] is True
    assert stored["ok"] is True
    assert "short" in recalled["speakable_summary"]


def test_openai_native_provider_is_exported_from_root():
    assert dhee.OpenAIAgent is OpenAIAgent
    assert dhee.OpenAIResponsesAgent is OpenAIResponsesAgent


def test_openai_native_provider_builds_response_kwargs(tmp_path):
    agent = OpenAIAgent(
        user_id="user_123",
        model="gpt-4.1",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    kwargs = agent.response_create_kwargs(
        input="What do you remember about me?",
        base_instruction="You are Chotu.",
        tools=[{"type": "web_search_preview"}],
        parallel_tool_calls=False,
    )

    assert kwargs["model"] == "gpt-4.1"
    assert kwargs["input"] == "What do you remember about me?"
    assert "You are Chotu." in kwargs["instructions"]
    assert "Dhee Memory Context" in kwargs["instructions"]
    assert kwargs["tools"][0]["name"] == "dhee_memory"
    assert kwargs["tools"][1]["type"] == "web_search_preview"
    assert kwargs["parallel_tool_calls"] is False


def test_openai_native_sdk_bridge_can_create_response(tmp_path, monkeypatch):
    installed = install_fake_openai_sdk(monkeypatch)
    agent = OpenAIAgent(
        user_id="user_123",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    client = agent.create_client(api_key="test-key")
    response = agent.create_response("Please remember short answers.", client=client)
    outputs = agent.function_call_outputs(response)
    finished = agent.finish("OpenAI session completed.")

    assert installed["client"].api_key == "test-key"
    assert response.output_text == "OpenAI response"
    assert client.responses.calls[0]["model"] == "gpt-4.1"
    assert client.responses.calls[0]["tools"][0]["name"] == "dhee_memory"
    assert outputs[0]["type"] == "function_call_output"
    assert outputs[0]["call_id"] == "call_123"
    assert json.loads(outputs[0]["output"])["ok"] is True
    assert finished["metadata"]["provider"] == "openai"


def test_openai_native_provider_does_not_define_forbidden_client_wrappers():
    source = "\n".join(
        path.read_text()
        for path in [
            __import__("pathlib").Path("dhee/providers/openai.py"),
            __import__("pathlib").Path("dhee/providers/base.py"),
        ]
    )

    assert "DheeElevenLabsClient" not in source
    assert "ElevenLabsClient" not in source
    assert "GeminiClient" not in source
    assert "OpenAIClient" not in source


def install_fake_openai_sdk(monkeypatch):
    installed = {}

    openai_mod = types.ModuleType("openai")

    class FakeToolCall:
        type = "function_call"
        name = "dhee_memory"
        call_id = "call_123"
        arguments = json.dumps(
            {"action": "remember", "content": "User prefers short answers."}
        )

    class FakeResponse:
        output_text = "OpenAI response"
        output = [FakeToolCall()]

    class FakeResponses:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return FakeResponse()

    class FakeOpenAI:
        def __init__(self, api_key=None, **kwargs):
            self.api_key = api_key
            self.kwargs = kwargs
            self.responses = FakeResponses()
            installed["client"] = self

    openai_mod.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", openai_mod)

    return installed
