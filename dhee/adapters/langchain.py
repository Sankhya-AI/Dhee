"""LangChain adapter — wraps DheePlugin tools as LangChain BaseTool instances.

Usage:
    from dhee import DheePlugin
    from dhee.adapters.langchain import get_dhee_tools

    plugin = DheePlugin()
    tools = get_dhee_tools(plugin)

    # Use with any LangChain agent:
    agent = create_react_agent(llm, tools)

    # Or pick individual tools:
    remember_tool, recall_tool, context_tool, checkpoint_tool = tools
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)

# Lazy import — LangChain is optional
_HAS_LANGCHAIN = None


def _check_langchain() -> bool:
    global _HAS_LANGCHAIN
    if _HAS_LANGCHAIN is None:
        try:
            from langchain_core.tools import BaseTool  # noqa: F401
            _HAS_LANGCHAIN = True
        except ImportError:
            _HAS_LANGCHAIN = False
    return _HAS_LANGCHAIN


def _get_base_classes():
    """Import LangChain base classes (raises ImportError if not installed)."""
    from langchain_core.tools import BaseTool
    from langchain_core.callbacks import CallbackManagerForToolRun
    try:
        from pydantic import BaseModel, Field
    except ImportError:
        from langchain_core.pydantic_v1 import BaseModel, Field
    return BaseTool, CallbackManagerForToolRun, BaseModel, Field


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _make_remember_tool(plugin: Any):
    BaseTool, CallbackManagerForToolRun, BaseModel, Field = _get_base_classes()

    class RememberInput(BaseModel):
        content: str = Field(description="The fact, preference, or observation to remember")
        user_id: Optional[str] = Field(default=None, description="User identifier")

    class DheeRemember(BaseTool):
        name: str = "dhee_remember"
        description: str = (
            "Store a fact, preference, or observation to memory. "
            "Zero LLM calls, one embedding call. Fast."
        )
        args_schema: Type[BaseModel] = RememberInput
        _plugin: Any = None

        def __init__(self, plugin: Any, **kwargs):
            super().__init__(**kwargs)
            self._plugin = plugin

        def _run(
            self,
            content: str,
            user_id: Optional[str] = None,
            run_manager: Optional[CallbackManagerForToolRun] = None,
        ) -> str:
            result = self._plugin.remember(content=content, user_id=user_id)
            return json.dumps(result, default=str)

    return DheeRemember(plugin=plugin)


def _make_recall_tool(plugin: Any):
    BaseTool, CallbackManagerForToolRun, BaseModel, Field = _get_base_classes()

    class RecallInput(BaseModel):
        query: str = Field(description="What you're trying to remember")
        user_id: Optional[str] = Field(default=None, description="User identifier")
        limit: int = Field(default=5, description="Maximum results to return")

    class DheeRecall(BaseTool):
        name: str = "dhee_recall"
        description: str = (
            "Search memory for relevant facts. Returns top-K results "
            "ranked by relevance. Zero LLM calls, one embedding."
        )
        args_schema: Type[BaseModel] = RecallInput
        _plugin: Any = None

        def __init__(self, plugin: Any, **kwargs):
            super().__init__(**kwargs)
            self._plugin = plugin

        def _run(
            self,
            query: str,
            user_id: Optional[str] = None,
            limit: int = 5,
            run_manager: Optional[CallbackManagerForToolRun] = None,
        ) -> str:
            results = self._plugin.recall(query=query, user_id=user_id, limit=limit)
            return json.dumps(results, default=str)

    return DheeRecall(plugin=plugin)


def _make_context_tool(plugin: Any):
    BaseTool, CallbackManagerForToolRun, BaseModel, Field = _get_base_classes()

    class ContextInput(BaseModel):
        task_description: Optional[str] = Field(
            default=None, description="What you're about to work on",
        )
        user_id: Optional[str] = Field(default=None, description="User identifier")

    class DheeContext(BaseTool):
        name: str = "dhee_context"
        description: str = (
            "HyperAgent session bootstrap. Returns performance snapshots, "
            "insights, intentions, warnings, heuristics, and relevant memories. "
            "Call once at the start of a task."
        )
        args_schema: Type[BaseModel] = ContextInput
        _plugin: Any = None

        def __init__(self, plugin: Any, **kwargs):
            super().__init__(**kwargs)
            self._plugin = plugin

        def _run(
            self,
            task_description: Optional[str] = None,
            user_id: Optional[str] = None,
            run_manager: Optional[CallbackManagerForToolRun] = None,
        ) -> str:
            result = self._plugin.context(
                task_description=task_description, user_id=user_id,
            )
            return json.dumps(result, default=str)

    return DheeContext(plugin=plugin)


def _make_checkpoint_tool(plugin: Any):
    BaseTool, CallbackManagerForToolRun, BaseModel, Field = _get_base_classes()

    class CheckpointInput(BaseModel):
        summary: str = Field(description="What you were working on")
        task_type: Optional[str] = Field(
            default=None, description="Task category (e.g., 'bug_fix')",
        )
        outcome_score: Optional[float] = Field(
            default=None, description="0.0-1.0 outcome score",
        )
        what_worked: Optional[str] = Field(
            default=None, description="Approach that worked",
        )
        what_failed: Optional[str] = Field(
            default=None, description="Approach that failed",
        )
        remember_to: Optional[str] = Field(
            default=None, description="Future intention: 'remember to X when Y'",
        )

    class DheeCheckpoint(BaseTool):
        name: str = "dhee_checkpoint"
        description: str = (
            "Save session state and learnings. Records outcomes, "
            "synthesizes insights from what worked/failed, stores intentions."
        )
        args_schema: Type[BaseModel] = CheckpointInput
        _plugin: Any = None

        def __init__(self, plugin: Any, **kwargs):
            super().__init__(**kwargs)
            self._plugin = plugin

        def _run(
            self,
            summary: str,
            task_type: Optional[str] = None,
            outcome_score: Optional[float] = None,
            what_worked: Optional[str] = None,
            what_failed: Optional[str] = None,
            remember_to: Optional[str] = None,
            run_manager: Optional[CallbackManagerForToolRun] = None,
        ) -> str:
            result = self._plugin.checkpoint(
                summary=summary, task_type=task_type,
                outcome_score=outcome_score, what_worked=what_worked,
                what_failed=what_failed, remember_to=remember_to,
            )
            return json.dumps(result, default=str)

    return DheeCheckpoint(plugin=plugin)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_dhee_tools(plugin: Any) -> List[Any]:
    """Create LangChain tool instances from a DheePlugin.

    Returns:
        [DheeRemember, DheeRecall, DheeContext, DheeCheckpoint]

    Raises:
        ImportError: If langchain-core is not installed.
    """
    if not _check_langchain():
        raise ImportError(
            "langchain-core is required for LangChain integration. "
            "Install it with: pip install langchain-core"
        )

    return [
        _make_remember_tool(plugin),
        _make_recall_tool(plugin),
        _make_context_tool(plugin),
        _make_checkpoint_tool(plugin),
    ]
