"""Dhee adapters — universal plugin interface for any agent framework.

Available adapters:
  - DheePlugin: Base universal plugin (remember/recall/context/checkpoint)
  - OpenAIToolAdapter: OpenAI function calling (tools= parameter)
  - get_dhee_tools: LangChain BaseTool wrappers
  - get_autogen_functions: AutoGen v0.2 callables + schemas
  - generate_snapshot: Frozen system prompt for non-tool-calling agents
"""

from dhee.adapters.base import DheePlugin

__all__ = ["DheePlugin"]
