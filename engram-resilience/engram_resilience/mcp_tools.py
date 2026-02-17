"""MCP tool definitions for engram-resilience."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register resilience MCP tools on the given server."""
    # Lazy state — fallback chain created on first configure call
    _state: dict[str, Any] = {"chain": None}

    tool_defs = {
        "configure_fallback": {
            "description": "Set up a model fallback chain with cascading providers",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "providers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "provider": {"type": "string"},
                                "model": {"type": "string"},
                            },
                            "required": ["provider", "model"],
                        },
                        "description": "Ordered list of fallback providers",
                    },
                },
                "required": ["providers"],
            },
        },
        "fallback_status": {
            "description": "Check current provider, fallback count, and error history",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        "compact_context": {
            "description": "Summarize a long conversation history to fit within token limits",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string"},
                                "content": {"type": "string"},
                            },
                        },
                        "description": "Conversation messages to compact",
                    },
                    "max_tokens": {"type": "integer", "description": "Target token limit", "default": 4000},
                    "keep_recent": {"type": "integer", "description": "Number of recent messages to keep verbatim", "default": 5},
                },
                "required": ["messages"],
            },
        },
        "retry_with_fallback": {
            "description": "Execute an operation with retry and fallback logic (for testing/diagnostics)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Prompt to generate with retry logic"},
                    "max_retries": {"type": "integer", "description": "Max retry attempts", "default": 3},
                },
                "required": ["prompt"],
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        if name == "configure_fallback":
            from engram_resilience.fallback import FallbackChain
            _state["chain"] = FallbackChain(args["providers"], memory=memory)
            return {"status": "configured", "providers": len(args["providers"])}

        elif name == "fallback_status":
            chain = _state.get("chain")
            if not chain:
                return {"error": "No fallback chain configured. Call configure_fallback first."}
            return chain.status()

        elif name == "compact_context":
            from engram_resilience.compaction import ContextCompactor
            llm = getattr(memory, "_llm", None)
            if not llm:
                return {"error": "No LLM available for compaction"}
            compactor = ContextCompactor(
                llm,
                max_tokens=args.get("max_tokens", 4000),
                keep_recent=args.get("keep_recent", 5),
            )
            result = compactor.compact(args["messages"])
            return {"messages": result, "original_count": len(args["messages"]),
                    "compacted_count": len(result)}

        elif name == "retry_with_fallback":
            chain = _state.get("chain")
            if not chain:
                return {"error": "No fallback chain configured. Call configure_fallback first."}
            from engram_resilience.retry import SmartRetry
            retry = SmartRetry(max_retries=args.get("max_retries", 3))
            result = retry.execute(chain.generate, args["prompt"])
            return {"result": result}

        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_resilience_tools"):
        server._resilience_tools = {}
    server._resilience_tools.update(tool_defs)
    server._resilience_handler = _handle
