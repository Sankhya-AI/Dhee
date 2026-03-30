"""AutoGen adapter — wraps DheePlugin tools as AutoGen-callable functions.

Supports both AutoGen v0.2 (register_for_llm/register_for_execution) and
the newer AG2 / AutoGen 0.4+ patterns.

Usage with AutoGen v0.2:
    from dhee import DheePlugin
    from dhee.adapters.autogen import get_autogen_functions, register_dhee_tools

    plugin = DheePlugin()

    # Option 1: Get callables + schemas for manual registration
    functions = get_autogen_functions(plugin)

    # Option 2: Auto-register on an assistant + executor pair
    register_dhee_tools(plugin, assistant=assistant, executor=user_proxy)

Usage with AG2 / AutoGen 0.4+:
    from dhee.adapters.autogen import get_autogen_tool_specs

    specs = get_autogen_tool_specs(plugin)
    # Pass to ConversableAgent(tools=specs)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool callables
# ---------------------------------------------------------------------------

def _make_callables(plugin: Any) -> Dict[str, Callable]:
    """Create plain callables wrapping DheePlugin methods."""

    def remember(content: str, user_id: str = "default") -> str:
        """Store a fact, preference, or observation to memory."""
        result = plugin.remember(content=content, user_id=user_id)
        return json.dumps(result, default=str)

    def recall(query: str, user_id: str = "default", limit: int = 5) -> str:
        """Search memory for relevant facts."""
        results = plugin.recall(query=query, user_id=user_id, limit=limit)
        return json.dumps(results, default=str)

    def context(
        task_description: str = "", user_id: str = "default",
    ) -> str:
        """HyperAgent session bootstrap. Returns full cognition context."""
        result = plugin.context(
            task_description=task_description or None, user_id=user_id,
        )
        return json.dumps(result, default=str)

    def checkpoint(
        summary: str,
        task_type: str = "",
        outcome_score: float = -1.0,
        what_worked: str = "",
        what_failed: str = "",
        remember_to: str = "",
    ) -> str:
        """Save session state and learnings."""
        kwargs: Dict[str, Any] = {"summary": summary}
        if task_type:
            kwargs["task_type"] = task_type
        if outcome_score >= 0:
            kwargs["outcome_score"] = outcome_score
        if what_worked:
            kwargs["what_worked"] = what_worked
        if what_failed:
            kwargs["what_failed"] = what_failed
        if remember_to:
            kwargs["remember_to"] = remember_to
        result = plugin.checkpoint(**kwargs)
        return json.dumps(result, default=str)

    return {
        "dhee_remember": remember,
        "dhee_recall": recall,
        "dhee_context": context,
        "dhee_checkpoint": checkpoint,
    }


# ---------------------------------------------------------------------------
# AutoGen v0.2 schemas
# ---------------------------------------------------------------------------

_AUTOGEN_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "dhee_remember",
        "description": (
            "Store a fact, preference, or observation to memory. "
            "Zero LLM calls, one embedding call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact to remember",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier (default: 'default')",
                    "default": "default",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "dhee_recall",
        "description": (
            "Search memory for relevant facts. Returns top-K ranked by relevance."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you're trying to remember",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier",
                    "default": "default",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "dhee_context",
        "description": (
            "HyperAgent session bootstrap. Returns performance, insights, "
            "intentions, warnings, heuristics, and memories."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "What you're about to work on",
                    "default": "",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier",
                    "default": "default",
                },
            },
        },
    },
    {
        "name": "dhee_checkpoint",
        "description": (
            "Save session state and learnings. Records outcomes, synthesizes "
            "insights, stores intentions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "What you were working on",
                },
                "task_type": {
                    "type": "string",
                    "description": "Task category (e.g., 'bug_fix')",
                    "default": "",
                },
                "outcome_score": {
                    "type": "number",
                    "description": "0.0-1.0 outcome score (-1 to skip)",
                    "default": -1.0,
                },
                "what_worked": {
                    "type": "string",
                    "description": "Approach that worked",
                    "default": "",
                },
                "what_failed": {
                    "type": "string",
                    "description": "Approach that failed",
                    "default": "",
                },
                "remember_to": {
                    "type": "string",
                    "description": "Future intention: 'remember to X when Y'",
                    "default": "",
                },
            },
            "required": ["summary"],
        },
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_autogen_functions(
    plugin: Any,
) -> List[Tuple[Callable, Dict[str, Any]]]:
    """Get (callable, schema) pairs for AutoGen v0.2 registration.

    Returns:
        List of (function, schema_dict) tuples ready for
        register_for_llm / register_for_execution.
    """
    callables = _make_callables(plugin)
    return [
        (callables[schema["name"]], schema)
        for schema in _AUTOGEN_SCHEMAS
    ]


def register_dhee_tools(
    plugin: Any,
    assistant: Any,
    executor: Any,
) -> None:
    """Register Dhee tools on an AutoGen v0.2 assistant + executor pair.

    Args:
        plugin: A DheePlugin instance.
        assistant: An AssistantAgent (or ConversableAgent) for LLM.
        executor: A UserProxyAgent (or ConversableAgent) for execution.
    """
    callables = _make_callables(plugin)

    for schema in _AUTOGEN_SCHEMAS:
        name = schema["name"]
        fn = callables[name]

        # Register for LLM (tool definition)
        assistant.register_for_llm(
            name=name,
            description=schema["description"],
        )(fn)

        # Register for execution
        executor.register_for_execution(name=name)(fn)


def get_autogen_tool_specs(plugin: Any) -> List[Dict[str, Any]]:
    """Get tool specs for AG2 / AutoGen 0.4+ ConversableAgent(tools=...).

    Returns a list of dicts with 'function' and 'schema' keys.
    """
    callables = _make_callables(plugin)
    return [
        {"function": callables[schema["name"]], "schema": schema}
        for schema in _AUTOGEN_SCHEMAS
    ]
