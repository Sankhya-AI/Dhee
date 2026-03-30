"""OpenAI function calling adapter for DheePlugin.

Generates tool definitions compatible with:
  - OpenAI Chat Completions API (tools parameter)
  - Any OpenAI-compatible API (Ollama, vLLM, LiteLLM, etc.)

Usage with the OpenAI SDK:
    from dhee.adapters.openai_funcs import OpenAIToolAdapter

    adapter = OpenAIToolAdapter(plugin)
    response = client.chat.completions.create(
        model="gpt-4",
        messages=messages,
        tools=adapter.tool_definitions(),
    )

    # Execute the function call
    for call in response.choices[0].message.tool_calls:
        result = adapter.execute(call.function.name, json.loads(call.function.arguments))
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class OpenAIToolAdapter:
    """Wraps DheePlugin as OpenAI-compatible function calling tools.

    Provides tool_definitions() for the API request and execute() for
    dispatching tool calls from the response.
    """

    def __init__(self, plugin: Any):
        """
        Args:
            plugin: A DheePlugin instance.
        """
        self._plugin = plugin
        self._dispatchers: Dict[str, Callable] = {
            "remember": self._exec_remember,
            "recall": self._exec_recall,
            "context": self._exec_context,
            "checkpoint": self._exec_checkpoint,
            "session_start": self._exec_session_start,
            "session_end": self._exec_session_end,
        }

    def tool_definitions(self, include_session: bool = False) -> List[Dict[str, Any]]:
        """Return OpenAI-format tool definitions.

        Args:
            include_session: If True, also includes session_start and
                session_end as callable tools.
        """
        tools = self._plugin.as_openai_functions()

        if include_session:
            tools.extend([
                {
                    "type": "function",
                    "function": {
                        "name": "session_start",
                        "description": (
                            "Start a Dhee cognition session. Returns a frozen context "
                            "block. Call once at the beginning of a task."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "task_description": {
                                    "type": "string",
                                    "description": "What you're about to work on",
                                },
                            },
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "session_end",
                        "description": (
                            "End the current Dhee session and save learnings."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "summary": {
                                    "type": "string",
                                    "description": "What you accomplished",
                                },
                                "outcome_score": {
                                    "type": "number",
                                    "description": "0.0-1.0 outcome score",
                                },
                                "what_worked": {
                                    "type": "string",
                                    "description": "Approach that worked",
                                },
                                "what_failed": {
                                    "type": "string",
                                    "description": "Approach that failed",
                                },
                            },
                            "required": ["summary"],
                        },
                    },
                },
            ])

        return tools

    def execute(self, function_name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool call and return the JSON-encoded result.

        This is the glue between OpenAI's tool_call response and DheePlugin.

        Args:
            function_name: The function name from the tool call.
            arguments: Parsed JSON arguments from the tool call.

        Returns:
            JSON string suitable for a tool message.
        """
        dispatcher = self._dispatchers.get(function_name)
        if not dispatcher:
            return json.dumps({"error": f"Unknown function: {function_name}"})

        try:
            result = dispatcher(arguments)
            return json.dumps(result, default=str, ensure_ascii=False)
        except Exception as e:
            logger.warning("Tool execution failed for %s: %s", function_name, e)
            return json.dumps({"error": str(e)})

    def _exec_remember(self, args: Dict[str, Any]) -> Any:
        return self._plugin.remember(
            content=args["content"],
            user_id=args.get("user_id"),
        )

    def _exec_recall(self, args: Dict[str, Any]) -> Any:
        return self._plugin.recall(
            query=args["query"],
            user_id=args.get("user_id"),
            limit=args.get("limit", 5),
        )

    def _exec_context(self, args: Dict[str, Any]) -> Any:
        return self._plugin.context(
            task_description=args.get("task_description"),
            user_id=args.get("user_id"),
        )

    def _exec_checkpoint(self, args: Dict[str, Any]) -> Any:
        return self._plugin.checkpoint(
            summary=args["summary"],
            task_type=args.get("task_type"),
            outcome_score=args.get("outcome_score"),
            what_worked=args.get("what_worked"),
            what_failed=args.get("what_failed"),
            remember_to=args.get("remember_to"),
            trigger_keywords=args.get("trigger_keywords"),
        )

    def _exec_session_start(self, args: Dict[str, Any]) -> Any:
        prompt = self._plugin.session_start(
            task_description=args.get("task_description"),
        )
        return {"system_prompt": prompt}

    def _exec_session_end(self, args: Dict[str, Any]) -> Any:
        return self._plugin.session_end(
            summary=args["summary"],
            outcome_score=args.get("outcome_score"),
            what_worked=args.get("what_worked"),
            what_failed=args.get("what_failed"),
        )
