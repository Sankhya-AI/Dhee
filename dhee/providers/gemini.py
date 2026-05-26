"""Native Gemini API agent integration for Dhee 7.2.0.

This provider maps Gemini API concepts to Dhee's universal runtime:
system instructions, function tools, model events, and finish checkpoints. It
uses the optional `google-genai` SDK when users want a native bridge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from dhee.profiles import gemini
from dhee.providers.base import ProviderMemoryRuntime


class GeminiAgent:
    """Dhee-powered memory adapter for a Gemini API agent."""

    def __init__(
        self,
        user_id: str,
        app_id: str = "gemini:agent",
        model: str = "gemini-2.5-flash",
        task: str = "gemini api agent session",
        run_id: Optional[str] = None,
        data_dir: Optional[Union[str, Path]] = None,
        in_memory: bool = False,
        offline: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ):
        self.model = model
        provider_metadata = {"provider": "gemini", "model": model}
        provider_metadata.update(metadata or {})
        self.memory = ProviderMemoryRuntime(
            user_id=user_id,
            app_id=app_id,
            task=task,
            channel="chat",
            run_id=run_id,
            data_dir=data_dir,
            in_memory=in_memory,
            offline=offline,
            metadata=provider_metadata,
        )

    def start(
        self,
        input: Optional[str] = None,
        budget_tokens: int = 900,
    ) -> dict[str, Any]:
        patch = self.memory.start(input=input, budget_tokens=budget_tokens)
        return {
            "run_id": patch.run_id,
            "user_id": patch.user_id,
            "app_id": patch.app_id,
            "context": patch.context,
            "dynamic_variables": patch.dynamic_variables,
            "metadata": patch.metadata,
        }

    def prompt_snippet(self) -> str:
        return gemini.prompt_snippet()

    def system_instruction(
        self,
        base_instruction: str = "You are a helpful Gemini API agent.",
        input: Optional[str] = None,
        budget_tokens: int = 900,
    ) -> str:
        self._ensure_started(input=input, budget_tokens=budget_tokens)
        return (
            f"{base_instruction.strip()}\n\n"
            f"{gemini.prompt_snippet(variable_name='dhee_context')}"
        ).replace("{{dhee_context}}", self.memory.patch.context)

    def function_declaration(self) -> dict[str, Any]:
        return gemini.function_declaration()

    def generate_content_config(
        self,
        base_instruction: str = "You are a helpful Gemini API agent.",
        input: Optional[str] = None,
        budget_tokens: int = 900,
        tools: Optional[list[Any]] = None,
        automatic_function_calling: bool = True,
        **config_kwargs: Any,
    ) -> Any:
        """Build a google-genai GenerateContentConfig wired to Dhee."""

        sdk = self._load_genai_sdk()
        dhee_tool = self.dhee_memory if automatic_function_calling else self._manual_tool()
        all_tools = [dhee_tool]
        if tools:
            all_tools.extend(tools)

        return sdk["types"].GenerateContentConfig(
            system_instruction=self.system_instruction(
                base_instruction=base_instruction,
                input=input,
                budget_tokens=budget_tokens,
            ),
            tools=all_tools,
            **config_kwargs,
        )

    def generation_config(self, *args: Any, **kwargs: Any) -> Any:
        """Alias for users who call Gemini config generation by this name."""

        return self.generate_content_config(*args, **kwargs)

    def create_client(self, api_key: Optional[str] = None, **client_kwargs: Any) -> Any:
        sdk = self._load_genai_sdk()
        if api_key:
            client_kwargs.setdefault("api_key", api_key)
        return sdk["genai"].Client(**client_kwargs)

    def generate_content(
        self,
        contents: Any,
        client: Optional[Any] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_instruction: str = "You are a helpful Gemini API agent.",
        tools: Optional[list[Any]] = None,
        automatic_function_calling: bool = True,
        budget_tokens: int = 900,
        **generate_kwargs: Any,
    ) -> Any:
        """Call `client.models.generate_content` with Dhee memory attached."""

        gemini_client = client or self.create_client(api_key=api_key)
        config = generate_kwargs.pop("config", None)
        if config is None:
            config = self.generate_content_config(
                base_instruction=base_instruction,
                input=self._content_to_text(contents),
                budget_tokens=budget_tokens,
                tools=tools,
                automatic_function_calling=automatic_function_calling,
            )
        else:
            self._ensure_started(input=self._content_to_text(contents))
        self.record_user_content(contents)
        response = gemini_client.models.generate_content(
            model=model or self.model,
            contents=contents,
            config=config,
            **generate_kwargs,
        )
        self.record_model_response(response)
        return response

    def dhee_memory(
        self,
        action: str,
        query: str = "",
        content: str = "",
        summary: str = "",
    ) -> dict[str, Any]:
        """Recall, store, correct, or checkpoint durable Dhee memory."""

        return self.handle_function_call(
            {
                "action": action,
                "query": query,
                "content": content,
                "summary": summary,
            }
        )

    def handle_function_call(self, function_call: Any) -> dict[str, Any]:
        name = getattr(function_call, "name", None)
        args = getattr(function_call, "args", None)
        if isinstance(function_call, dict):
            name = function_call.get("name", name)
            args = function_call.get("args", function_call)
        if name and name != "dhee_memory":
            return {"ok": False, "error": f"Unknown Gemini function: {name}"}

        payload = dict(args or {})
        return self.memory.tool(
            action=payload.get("action") or "recall",
            query=payload.get("query") or "",
            content=payload.get("content") or "",
            summary=payload.get("summary") or "",
            metadata={"provider": "gemini", "model": self.model},
        )

    def record_user_content(self, contents: Any) -> dict[str, Any]:
        return self.memory.event(
            "gemini.user_content",
            content=self._content_to_text(contents),
            metadata={"provider": "gemini", "model": self.model},
        )

    def record_model_response(self, response: Any) -> dict[str, Any]:
        text = getattr(response, "text", None) or self._content_to_text(response)
        return self.memory.event(
            "gemini.model_response",
            content=text,
            metadata={"provider": "gemini", "model": self.model},
        )

    def finish(
        self,
        summary: str,
        outcome: str = "completed",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        final_metadata = {"provider": "gemini", "model": self.model}
        final_metadata.update(metadata or {})
        return self.memory.finish(
            outcome=outcome,
            summary=summary,
            metadata=final_metadata,
        )

    def _manual_tool(self) -> Any:
        sdk = self._load_genai_sdk()
        declaration = sdk["types"].FunctionDeclaration(
            name="dhee_memory",
            description="Recall, store, correct, or checkpoint durable user memory.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["recall", "remember", "correct", "checkpoint"],
                    },
                    "query": {"type": "string"},
                    "content": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["action"],
            },
        )
        return sdk["types"].Tool(function_declarations=[declaration])

    def _ensure_started(
        self,
        input: Optional[str] = None,
        budget_tokens: int = 900,
    ) -> None:
        if self.memory.patch is None:
            self.start(input=input, budget_tokens=budget_tokens)

    @staticmethod
    def _content_to_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(GeminiAgent._content_to_text(item) for item in value)
        text = getattr(value, "text", None)
        if text:
            return str(text)
        return str(value)

    @staticmethod
    def _load_genai_sdk() -> dict[str, Any]:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ImportError(
                "Install the Gemini SDK to use native sessions: "
                "pip install google-genai"
            ) from exc

        return {"genai": genai, "types": types}


GeminiAPIAgent = GeminiAgent
