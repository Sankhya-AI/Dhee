"""Native ElevenLabs agent integration for Dhee 7.2.0.

This provider maps ElevenLabs agent concepts to Dhee's universal runtime:
dynamic variables, the `dhee_memory` server tool, transcript events, and
post-call checkpoints. It can also create an optional ElevenLabs SDK
Conversation when the `elevenlabs` package is installed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional, Union

from dhee.profiles import elevenlabs
from dhee.providers.base import ProviderMemoryRuntime
from dhee.webhooks.elevenlabs import summary_from_analysis, transcript_to_events


class ElevenLabsAgent:
    """Dhee-powered memory adapter for an ElevenLabs voice agent."""

    def __init__(
        self,
        public_base_url: str,
        user_id: str,
        agent_id: str = "agent",
        task: str = "voice call",
        run_id: Optional[str] = None,
        data_dir: Optional[Union[str, Path]] = None,
        in_memory: bool = False,
        offline: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ):
        self.public_base_url = public_base_url.rstrip("/")
        self.agent_id = agent_id
        self.app_id = f"elevenlabs:{agent_id}"
        provider_metadata = {"provider": "elevenlabs", "agent_id": agent_id}
        provider_metadata.update(metadata or {})
        self.memory = ProviderMemoryRuntime(
            user_id=user_id,
            app_id=self.app_id,
            task=task,
            channel="voice",
            run_id=run_id,
            data_dir=data_dir,
            in_memory=in_memory,
            offline=offline,
            metadata=provider_metadata,
        )

    def prompt_snippet(self) -> str:
        return elevenlabs.prompt_snippet()

    def server_tool_schema(self) -> dict[str, Any]:
        return elevenlabs.server_tool_schema(self.public_base_url)

    def post_call_webhook_url(self) -> str:
        return f"{self.public_base_url}/v1/webhooks/elevenlabs/post_call"

    def start_call(
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
            "dynamic_variables": elevenlabs.dynamic_variables_from_patch(patch),
            "metadata": patch.metadata,
        }

    def conversation_initiation_data(
        self,
        input: Optional[str] = None,
        budget_tokens: int = 900,
    ) -> Any:
        """Build ElevenLabs SDK initiation data with Dhee dynamic variables."""

        self._ensure_started(input=input, budget_tokens=budget_tokens)
        sdk = self._load_conversation_sdk()
        return sdk["ConversationInitiationData"](
            dynamic_variables=self.memory.dynamic_variables()
        )

    def client_tools(self, existing_client_tools: Optional[Any] = None) -> Any:
        """Register `dhee_memory` as an ElevenLabs SDK client tool."""

        sdk = self._load_conversation_sdk()
        tools = existing_client_tools or sdk["ClientTools"]()
        tools.register("dhee_memory", self._client_tool_dhee_memory, is_async=False)
        return tools

    def wrap_user_transcript(
        self,
        callback: Optional[Callable[..., Any]] = None,
    ) -> Callable[..., Any]:
        """Wrap an existing user transcript callback so Dhee observes it."""

        return self._wrap_user_transcript(callback)

    def wrap_agent_response(
        self,
        callback: Optional[Callable[..., Any]] = None,
    ) -> Callable[..., Any]:
        """Wrap an existing agent response callback so Dhee observes it."""

        return self._wrap_agent_response(callback)

    def wrap_agent_response_correction(
        self,
        callback: Optional[Callable[..., Any]] = None,
    ) -> Callable[..., Any]:
        """Wrap an existing response-correction callback so Dhee observes it."""

        return self._wrap_agent_response_correction(callback)

    def create_conversation(
        self,
        api_key: Optional[str] = None,
        client: Optional[Any] = None,
        audio_interface: Optional[Any] = None,
        requires_auth: Optional[bool] = None,
        client_tools: Optional[Any] = None,
        callback_agent_response: Optional[Callable[..., Any]] = None,
        callback_agent_response_correction: Optional[Callable[..., Any]] = None,
        callback_user_transcript: Optional[Callable[..., Any]] = None,
        callback_latency_measurement: Optional[Callable[..., Any]] = None,
        input: Optional[str] = None,
        budget_tokens: int = 900,
        **conversation_kwargs: Any,
    ) -> Any:
        """Create an ElevenLabs SDK Conversation wired to Dhee memory."""

        sdk = self._load_conversation_sdk()
        elevenlabs_client = client or self._create_sdk_client(api_key=api_key)
        audio = audio_interface or sdk["DefaultAudioInterface"]()
        auth_required = (
            bool(api_key or os.getenv("ELEVENLABS_API_KEY"))
            if requires_auth is None
            else requires_auth
        )

        return sdk["Conversation"](
            client=elevenlabs_client,
            agent_id=self.agent_id,
            config=self.conversation_initiation_data(
                input=input,
                budget_tokens=budget_tokens,
            ),
            requires_auth=auth_required,
            audio_interface=audio,
            client_tools=self.client_tools(client_tools),
            callback_agent_response=self.wrap_agent_response(callback_agent_response),
            callback_agent_response_correction=self.wrap_agent_response_correction(
                callback_agent_response_correction
            ),
            callback_user_transcript=self.wrap_user_transcript(callback_user_transcript),
            callback_latency_measurement=callback_latency_measurement,
            **conversation_kwargs,
        )

    def start_session(self, *args: Any, **kwargs: Any) -> Any:
        """Create and start an ElevenLabs SDK conversation session."""

        conversation = self.create_conversation(*args, **kwargs)
        conversation.start_session()
        return conversation

    def handle_memory_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(payload.get("metadata") or {})
        for key in ("conversation_id", "conversation_history"):
            if payload.get(key):
                metadata[key] = payload[key]

        return self.memory.tool(
            action=payload.get("action") or "recall",
            query=payload.get("query"),
            content=payload.get("content"),
            summary=payload.get("summary"),
            metadata=metadata,
        )

    def record_turn(
        self,
        role: str,
        message: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        role = (role or "unknown").lower()
        if role == "user":
            event_type = "voice.user_transcript"
        elif role in {"agent", "assistant"}:
            event_type = "voice.agent_response"
        else:
            event_type = f"voice.{role}"
        return self.memory.event(event_type, content=message, metadata=metadata)

    def checkpoint_post_call(self, event_or_data: dict[str, Any]) -> dict[str, Any]:
        data = event_or_data.get("data") or event_or_data
        for event in transcript_to_events(data):
            self.memory.event(
                event["type"],
                content=event["content"],
                metadata=event["metadata"],
            )

        analysis = data.get("analysis") or {}
        status = data.get("status")
        outcome = "completed" if status in {None, "done", "completed"} else str(status)
        return self.memory.finish(
            outcome=outcome,
            summary=summary_from_analysis(data),
            outcome_score=1.0 if analysis.get("call_successful") is True else None,
            metadata={
                "provider": "elevenlabs",
                "conversation_id": data.get("conversation_id"),
                "agent_id": data.get("agent_id") or self.agent_id,
            },
        )

    def finish_call(
        self,
        summary: str,
        outcome: str = "completed",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        final_metadata = {"provider": "elevenlabs", "agent_id": self.agent_id}
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
            self.start_call(input=input, budget_tokens=budget_tokens)

    def _client_tool_dhee_memory(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.handle_memory_tool(params or {})

    def _wrap_user_transcript(
        self,
        callback: Optional[Callable[..., Any]],
    ) -> Callable[..., Any]:
        def handler(transcript: Any) -> Any:
            self.record_turn("user", str(transcript))
            if callback:
                return callback(transcript)
            return None

        return handler

    def _wrap_agent_response(
        self,
        callback: Optional[Callable[..., Any]],
    ) -> Callable[..., Any]:
        def handler(response: Any) -> Any:
            self.record_turn("agent", str(response))
            if callback:
                return callback(response)
            return None

        return handler

    def _wrap_agent_response_correction(
        self,
        callback: Optional[Callable[..., Any]],
    ) -> Callable[..., Any]:
        def handler(original: Any, corrected: Any) -> Any:
            self.record_turn(
                "agent",
                str(corrected),
                metadata={"original_response": str(original), "corrected": True},
            )
            if callback:
                return callback(original, corrected)
            return None

        return handler

    @staticmethod
    def _load_conversation_sdk() -> dict[str, Any]:
        try:
            from elevenlabs.conversational_ai.conversation import (
                ClientTools,
                Conversation,
                ConversationInitiationData,
            )
            from elevenlabs.conversational_ai.default_audio_interface import (
                DefaultAudioInterface,
            )
        except ImportError as exc:
            raise ImportError(
                "Install the ElevenLabs SDK to use native sessions: "
                "pip install elevenlabs"
            ) from exc

        return {
            "ClientTools": ClientTools,
            "Conversation": Conversation,
            "ConversationInitiationData": ConversationInitiationData,
            "DefaultAudioInterface": DefaultAudioInterface,
        }

    @staticmethod
    def _create_sdk_client(api_key: Optional[str] = None) -> Any:
        try:
            from elevenlabs.client import ElevenLabs
        except ImportError as exc:
            raise ImportError(
                "Install the ElevenLabs SDK to use native sessions: "
                "pip install elevenlabs"
            ) from exc

        if api_key:
            return ElevenLabs(api_key=api_key)
        return ElevenLabs()


ElevenAgent = ElevenLabsAgent
