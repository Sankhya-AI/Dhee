"""Native OpenAI Responses API agent integration for Dhee 7.2+."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

from dhee.profiles import openai
from dhee.providers.base import ProviderMemoryRuntime


class OpenAIAgent:
    """Dhee-powered memory adapter for an OpenAI Responses API agent."""

    def __init__(
        self,
        user_id: str,
        app_id: str = "openai:agent",
        model: str = "gpt-4.1",
        task: str = "openai agent session",
        run_id: Optional[str] = None,
        data_dir: Optional[Union[str, Path]] = None,
        in_memory: bool = False,
        offline: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ):
        self.model = model
        provider_metadata = {"provider": "openai", "model": model}
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
        return openai.prompt_snippet()

    def instructions(
        self,
        base_instruction: str = "You are a helpful OpenAI agent.",
        input: Optional[str] = None,
        budget_tokens: int = 900,
    ) -> str:
        self._ensure_started(input=input, budget_tokens=budget_tokens)
        return (
            f"{base_instruction.strip()}\n\n"
            f"{openai.prompt_snippet(variable_name='dhee_context')}"
        ).replace("{{dhee_context}}", self.memory.patch.context)

    def tool_schema(self, strict: bool = True) -> dict[str, Any]:
        return openai.responses_tool_schema(strict=strict)

    def tools(
        self,
        existing_tools: Optional[list[dict[str, Any]]] = None,
        strict: bool = True,
    ) -> list[dict[str, Any]]:
        all_tools = [self.tool_schema(strict=strict)]
        if existing_tools:
            all_tools.extend(existing_tools)
        return all_tools

    def response_create_kwargs(
        self,
        input: Any,
        base_instruction: str = "You are a helpful OpenAI agent.",
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        strict: bool = True,
        budget_tokens: int = 900,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build kwargs for `client.responses.create(...)` with Dhee attached."""

        return {
            "model": model or self.model,
            "input": input,
            "instructions": self.instructions(
                base_instruction=base_instruction,
                input=self._content_to_text(input),
                budget_tokens=budget_tokens,
            ),
            "tools": self.tools(existing_tools=tools, strict=strict),
            **kwargs,
        }

    def create_client(self, api_key: Optional[str] = None, **client_kwargs: Any) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "Install the OpenAI SDK to use native sessions: pip install openai"
            ) from exc

        if api_key:
            client_kwargs.setdefault("api_key", api_key)
        return OpenAI(**client_kwargs)

    def create_response(
        self,
        input: Any,
        client: Optional[Any] = None,
        api_key: Optional[str] = None,
        base_instruction: str = "You are a helpful OpenAI agent.",
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        strict: bool = True,
        budget_tokens: int = 900,
        **kwargs: Any,
    ) -> Any:
        openai_client = client or self.create_client(api_key=api_key)
        request = self.response_create_kwargs(
            input=input,
            base_instruction=base_instruction,
            model=model,
            tools=tools,
            strict=strict,
            budget_tokens=budget_tokens,
            **kwargs,
        )
        self.record_user_input(input)
        response = openai_client.responses.create(**request)
        self.record_model_response(response)
        return response

    def handle_tool_call(self, tool_call: Any) -> dict[str, Any]:
        name = getattr(tool_call, "name", None)
        arguments = getattr(tool_call, "arguments", None)
        if isinstance(tool_call, dict):
            name = tool_call.get("name", name)
            arguments = tool_call.get("arguments", tool_call.get("args", arguments))
        if name and name != "dhee_memory":
            return {"ok": False, "error": f"Unknown OpenAI function: {name}"}

        payload = self._parse_arguments(arguments)
        return self.memory.tool(
            action=payload.get("action") or "recall",
            query=payload.get("query") or "",
            content=payload.get("content") or "",
            summary=payload.get("summary") or "",
            metadata={"provider": "openai", "model": self.model},
        )

    def function_call_output(
        self,
        tool_call: Any,
        result: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        call_id = self._call_id(tool_call)
        if not call_id:
            raise ValueError("OpenAI function call is missing call_id")
        output = result if result is not None else self.handle_tool_call(tool_call)
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(output),
        }

    def function_call_outputs(self, response: Any) -> list[dict[str, Any]]:
        return [
            self.function_call_output(item)
            for item in getattr(response, "output", []) or []
            if self._item_type(item) == "function_call"
        ]

    def record_user_input(self, input: Any) -> dict[str, Any]:
        return self.memory.event(
            "openai.user_input",
            content=self._content_to_text(input),
            metadata={"provider": "openai", "model": self.model},
        )

    def record_model_response(self, response: Any) -> dict[str, Any]:
        text = getattr(response, "output_text", None) or self._content_to_text(response)
        return self.memory.event(
            "openai.model_response",
            content=text,
            metadata={"provider": "openai", "model": self.model},
        )

    def finish(
        self,
        summary: str,
        outcome: str = "completed",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        final_metadata = {"provider": "openai", "model": self.model}
        final_metadata.update(metadata or {})
        return self.memory.finish(
            outcome=outcome,
            summary=summary,
            metadata=final_metadata,
        )

    def _ensure_started(
        self,
        input: Optional[str] = None,
        budget_tokens: int = 900,
    ) -> None:
        if self.memory.patch is None:
            self.start(input=input, budget_tokens=budget_tokens)

    @staticmethod
    def _parse_arguments(arguments: Any) -> dict[str, Any]:
        if arguments is None:
            return {}
        if isinstance(arguments, dict):
            return dict(arguments)
        if isinstance(arguments, str):
            return json.loads(arguments or "{}")
        return dict(arguments)

    @staticmethod
    def _call_id(tool_call: Any) -> Optional[str]:
        if isinstance(tool_call, dict):
            return tool_call.get("call_id")
        return getattr(tool_call, "call_id", None)

    @staticmethod
    def _item_type(item: Any) -> Optional[str]:
        if isinstance(item, dict):
            return item.get("type")
        return getattr(item, "type", None)

    @staticmethod
    def _content_to_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(OpenAIAgent._content_to_text(item) for item in value)
        text = getattr(value, "output_text", None) or getattr(value, "text", None)
        if text:
            return str(text)
        return str(value)


OpenAIResponsesAgent = OpenAIAgent
