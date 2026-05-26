from __future__ import annotations

import sys
import types

import dhee
from dhee.providers.gemini import GeminiAgent, GeminiAPIAgent


def test_gemini_native_provider_exposes_dhee_runtime(tmp_path):
    agent = GeminiAgent(
        user_id="user_123",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    started = agent.start()
    stored = agent.dhee_memory(
        action="remember",
        content="User prefers concise answers.",
    )
    recalled = agent.dhee_memory(
        action="recall",
        query="answer preference",
    )

    assert started["dynamic_variables"]["dhee_user_id"] == "user_123"
    assert started["dynamic_variables"]["dhee_app_id"] == "gemini:agent"
    assert agent.function_declaration()["name"] == "dhee_memory"
    assert stored["ok"] is True
    assert "concise" in recalled["speakable_summary"]


def test_gemini_native_provider_is_exported_from_root():
    assert dhee.GeminiAgent is GeminiAgent
    assert dhee.GeminiAPIAgent is GeminiAPIAgent


def test_gemini_native_sdk_bridge_builds_generate_config(tmp_path, monkeypatch):
    install_fake_google_genai(monkeypatch)
    agent = GeminiAPIAgent(
        user_id="user_123",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    config = agent.generate_content_config(base_instruction="You are Chotu.")
    manual_config = agent.generate_content_config(automatic_function_calling=False)

    assert "You are Chotu." in config.system_instruction
    assert "Dhee Memory Context" in config.system_instruction
    assert config.tools[0].__name__ == "dhee_memory"
    assert manual_config.tools[0].function_declarations[0].name == "dhee_memory"
    assert manual_config.tools[0].function_declarations[0].parameters["type"] == "object"


def test_gemini_native_sdk_bridge_can_call_generate_content(tmp_path, monkeypatch):
    installed = install_fake_google_genai(monkeypatch)
    agent = GeminiAgent(
        user_id="user_123",
        model="gemini-2.5-flash",
        data_dir=tmp_path / "dhee",
        in_memory=True,
        offline=True,
    )

    client = agent.create_client(api_key="test-key")
    response = agent.generate_content(
        "Please remember I prefer concise summaries.",
        client=client,
    )
    result = agent.handle_function_call(
        types.SimpleNamespace(
            name="dhee_memory",
            args={
                "action": "remember",
                "content": "User prefers concise summaries.",
            },
        )
    )
    finished = agent.finish("Gemini agent session completed.")

    assert installed["client"].api_key == "test-key"
    assert response.text == "Gemini response"
    assert client.models.calls[0]["model"] == "gemini-2.5-flash"
    assert client.models.calls[0]["config"].tools[0].__name__ == "dhee_memory"
    assert result["ok"] is True
    assert finished["metadata"]["provider"] == "gemini"


def test_gemini_native_provider_does_not_define_forbidden_client_wrappers():
    source = "\n".join(
        path.read_text()
        for path in [
            __import__("pathlib").Path("dhee/providers/gemini.py"),
            __import__("pathlib").Path("dhee/providers/base.py"),
        ]
    )

    assert "DheeElevenLabsClient" not in source
    assert "ElevenLabsClient" not in source
    assert "GeminiClient" not in source
    assert "OpenAIClient" not in source


def install_fake_google_genai(monkeypatch):
    installed = {}

    google_pkg = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.system_instruction = kwargs.get("system_instruction")
            self.tools = kwargs.get("tools")

    class FakeFunctionDeclaration:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.name = kwargs.get("name")
            self.description = kwargs.get("description")
            self.parameters = kwargs.get("parameters")

    class FakeTool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.function_declarations = kwargs.get("function_declarations")

    class FakeResponse:
        text = "Gemini response"

    class FakeModels:
        def __init__(self):
            self.calls = []

        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key=None, **kwargs):
            self.api_key = api_key
            self.kwargs = kwargs
            self.models = FakeModels()
            installed["client"] = self

    types_mod.GenerateContentConfig = FakeGenerateContentConfig
    types_mod.FunctionDeclaration = FakeFunctionDeclaration
    types_mod.Tool = FakeTool
    genai_mod.Client = FakeClient
    genai_mod.types = types_mod
    google_pkg.genai = genai_mod

    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)

    return installed
