"""DheePlugin — universal cognition plugin for any agent framework.

This is THE entry point for integrating Dhee into any agent. It wraps the
full Engram + Buddhi stack behind a framework-agnostic API that mirrors
the 4 MCP tools (remember/recall/context/checkpoint) and adds:

  - session_start/session_end lifecycle (Hermes-style frozen snapshot)
  - Trajectory recording for skill mining + self-evolution
  - Framework export helpers (OpenAI functions, system prompt block)

Usage:
    from dhee import DheePlugin

    # Zero-config (in-memory, mock provider)
    plugin = DheePlugin(in_memory=True)

    # Production (auto-detects provider from env)
    plugin = DheePlugin()

    # Edge/hardware (fully offline)
    plugin = DheePlugin(offline=True, data_dir="/data/dhee")

    # Framework integration
    tools = plugin.as_openai_functions()  # for OpenAI function calling
    prompt = plugin.session_start("fixing auth bug")  # frozen snapshot
"""

from __future__ import annotations

import json
import logging
import os
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class DheePlugin:
    """Universal cognition plugin that makes any agent a HyperAgent.

    Wraps Engram (memory) + Buddhi (cognition) behind 4 tools that work
    with MCP, OpenAI functions, LangChain, AutoGen, or direct Python.

    Args:
        data_dir: Storage directory. Defaults to ~/.dhee.
        provider: "openai", "gemini", "ollama", or None (auto-detect).
        user_id: Default user ID for all operations.
        in_memory: Use in-memory storage (for testing).
        offline: Force fully offline mode (no API calls).
        config: Override MemoryConfig directly.
    """

    def __init__(
        self,
        data_dir: Optional[Union[str, Path]] = None,
        provider: Optional[str] = None,
        user_id: str = "default",
        in_memory: bool = False,
        offline: bool = False,
        config=None,
    ):
        self._user_id = user_id
        self._offline = offline
        self._active_trajectories: Dict[str, Any] = {}

        # Resolve provider
        if offline and provider is None:
            provider = "mock"

        # Build the Engram (memory) layer
        from dhee.simple import Engram
        self._engram = Engram(
            provider=provider,
            data_dir=data_dir,
            in_memory=in_memory,
        )

        # Build the Buddhi (cognition) layer
        from dhee.core.buddhi import Buddhi
        buddhi_dir = str(self._engram.data_dir / "buddhi")
        self._buddhi = Buddhi(data_dir=buddhi_dir)

        # Session tracking
        self._session_id: Optional[str] = None
        self._session_start_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        return self._engram.data_dir

    @property
    def provider(self) -> str:
        return self._engram.provider

    @property
    def buddhi(self):
        return self._buddhi

    # ------------------------------------------------------------------
    # Tool 1: remember
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store a fact, preference, or observation.

        0 LLM calls on hot path. 1 embedding call. Intention auto-detection
        checks for "remember to X when Y" patterns.
        """
        uid = user_id or self._user_id
        result = self._engram.add(content, user_id=uid, infer=False, metadata=metadata)

        response: Dict[str, Any] = {"stored": True}
        if isinstance(result, dict):
            rs = result.get("results", [])
            if rs:
                response["id"] = rs[0].get("id")

        # Buddhi: detect intentions in the content
        intention = self._buddhi.on_memory_stored(content=content, user_id=uid)
        if intention:
            response["detected_intention"] = intention.to_dict()

        return response

    # ------------------------------------------------------------------
    # Tool 2: recall
    # ------------------------------------------------------------------

    def recall(
        self,
        query: str,
        user_id: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search memory for relevant facts. 0 LLM calls. 1 embedding."""
        uid = user_id or self._user_id
        results = self._engram.search(query, user_id=uid, limit=limit)
        return [
            {
                "memory": r.get("memory", r.get("content", "")),
                "score": round(r.get("composite_score", r.get("score", 0.0)), 3),
                "id": r.get("id", ""),
            }
            for r in results
        ]

    # ------------------------------------------------------------------
    # Tool 3: context
    # ------------------------------------------------------------------

    def context(
        self,
        task_description: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """HyperAgent session bootstrap. Returns everything the agent needs."""
        uid = user_id or self._user_id
        hyper_ctx = self._buddhi.get_hyper_context(
            user_id=uid,
            task_description=task_description,
            memory=self._engram._memory,
        )
        return hyper_ctx.to_dict()

    # ------------------------------------------------------------------
    # Tool 4: checkpoint
    # ------------------------------------------------------------------

    def checkpoint(
        self,
        summary: str,
        task_type: Optional[str] = None,
        outcome_score: Optional[float] = None,
        what_worked: Optional[str] = None,
        what_failed: Optional[str] = None,
        key_decision: Optional[str] = None,
        remember_to: Optional[str] = None,
        trigger_keywords: Optional[List[str]] = None,
        status: str = "paused",
        decisions: Optional[List[str]] = None,
        todos: Optional[List[str]] = None,
        files_touched: Optional[List[str]] = None,
        repo: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: str = "dhee",
    ) -> Dict[str, Any]:
        """Save session state. Where the cognition happens.

        1. Session digest → cross-agent handoff
        2. Batch enrichment → 1 LLM call per ~10 memories
        3. Outcome recording → performance tracking
        4. Insight synthesis → transferable learnings
        5. Intention storage → prospective memory
        """
        uid = user_id or self._user_id
        result: Dict[str, Any] = {}

        # 1. Session digest
        try:
            from dhee.core.kernel import save_session_digest
            digest = save_session_digest(
                task_summary=summary, agent_id=agent_id, repo=repo,
                status=status, decisions_made=decisions,
                files_touched=files_touched, todos_remaining=todos,
            )
            result["session_saved"] = True
            if isinstance(digest, dict):
                result["session_id"] = digest.get("session_id")
        except Exception:
            result["session_saved"] = False

        # 2. Batch enrichment
        memory = self._engram._memory
        if hasattr(memory, "enrich_pending"):
            try:
                enrich_result = memory.enrich_pending(
                    user_id=uid, batch_size=10, max_batches=5,
                )
                enriched = enrich_result.get("enriched_count", 0)
                if enriched > 0:
                    result["memories_enriched"] = enriched
            except Exception:
                pass

        # 3. Outcome recording
        if task_type and outcome_score is not None:
            score = max(0.0, min(1.0, float(outcome_score)))
            insight = self._buddhi.record_outcome(
                user_id=uid, task_type=task_type, score=score,
            )
            result["outcome_recorded"] = True
            if insight:
                result["auto_insight"] = insight.to_dict()

        # 4. Insight synthesis
        if any([what_worked, what_failed, key_decision]):
            insights = self._buddhi.reflect(
                user_id=uid, task_type=task_type or "general",
                what_worked=what_worked, what_failed=what_failed,
                key_decision=key_decision,
            )
            result["insights_created"] = len(insights)

        # 5. Intention storage
        if remember_to:
            intention = self._buddhi.store_intention(
                user_id=uid, description=remember_to,
                trigger_keywords=trigger_keywords,
            )
            result["intention_stored"] = intention.to_dict()

        return result

    # ------------------------------------------------------------------
    # Session lifecycle (Hermes-style frozen snapshot)
    # ------------------------------------------------------------------

    def session_start(
        self,
        task_description: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Start a session and return a frozen system prompt block.

        The system prompt contains the full HyperContext rendered as text.
        Inject it into your agent's system prompt at session start.
        The snapshot is frozen — writes during the session update storage
        but don't change this prompt, preserving LLM prefix caches.
        """
        uid = user_id or self._user_id
        self._session_id = str(uuid.uuid4())
        self._session_start_time = time.time()

        ctx = self.context(task_description=task_description, user_id=uid)
        return self._render_system_prompt(ctx, task_description)

    def session_end(
        self,
        summary: str,
        outcome_score: Optional[float] = None,
        task_type: Optional[str] = None,
        what_worked: Optional[str] = None,
        what_failed: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """End a session. Shorthand for checkpoint with session metadata."""
        result = self.checkpoint(
            summary=summary, outcome_score=outcome_score,
            task_type=task_type, what_worked=what_worked,
            what_failed=what_failed, status="completed", **kwargs,
        )
        self._session_id = None
        self._session_start_time = None
        return result

    # ------------------------------------------------------------------
    # Trajectory recording (for skill mining + self-evolution)
    # ------------------------------------------------------------------

    def begin_trajectory(
        self,
        task_description: str,
        user_id: Optional[str] = None,
        agent_id: str = "default",
    ):
        """Start recording a trajectory for this task.

        Returns a TrajectoryRecorder — call .record_step() on each action,
        then pass it to end_trajectory() when done.
        """
        from dhee.skills.trajectory import TrajectoryRecorder
        uid = user_id or self._user_id
        recorder = TrajectoryRecorder(
            task_description=task_description,
            user_id=uid,
            agent_id=agent_id,
        )
        self._active_trajectories[recorder.id] = recorder
        return recorder

    def end_trajectory(
        self,
        recorder,
        success: bool,
        outcome_summary: str = "",
    ) -> Dict[str, Any]:
        """Finalize a trajectory and feed it into the learning pipeline."""
        trajectory = recorder.finalize(success=success, outcome_summary=outcome_summary)
        self._active_trajectories.pop(recorder.id, None)

        result: Dict[str, Any] = {
            "trajectory_id": trajectory.id,
            "steps": len(trajectory.steps),
            "success": success,
        }

        # Store trajectory as memory for skill mining
        try:
            from dhee.skills.trajectory import TrajectoryStore
            store = TrajectoryStore(memory=self._engram._memory)
            store.save(trajectory)
            result["stored"] = True
        except Exception:
            result["stored"] = False

        return result

    # ------------------------------------------------------------------
    # Framework export: OpenAI function calling
    # ------------------------------------------------------------------

    def as_openai_functions(self) -> List[Dict[str, Any]]:
        """Return the 4 tools as OpenAI function calling schemas.

        Use with: client.chat.completions.create(tools=plugin.as_openai_functions())
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "remember",
                    "description": (
                        "Store a fact, preference, or observation to memory. "
                        "0 LLM calls, 1 embedding. Fast."
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
                            },
                        },
                        "required": ["content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "recall",
                    "description": (
                        "Search memory for relevant facts. Returns top-K ranked by relevance. "
                        "0 LLM calls, 1 embedding."
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
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max results (default: 5)",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "context",
                    "description": (
                        "HyperAgent session bootstrap. Returns performance, insights, "
                        "intentions, warnings, and memories. Call once at session start."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_description": {
                                "type": "string",
                                "description": "What you're about to work on",
                            },
                            "user_id": {
                                "type": "string",
                                "description": "User identifier",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "checkpoint",
                    "description": (
                        "Save session state and learnings. Records outcomes, "
                        "synthesizes insights, stores intentions."
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
                            "remember_to": {
                                "type": "string",
                                "description": "Future intention: 'remember to X when Y'",
                            },
                            "trigger_keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Keywords that trigger the intention",
                            },
                        },
                        "required": ["summary"],
                    },
                },
            },
        ]

    # ------------------------------------------------------------------
    # Framework export: system prompt
    # ------------------------------------------------------------------

    def as_system_prompt(
        self,
        task_description: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Generate a frozen system prompt block from current HyperContext.

        For agents that don't support tool calling — inject this into
        the system prompt so the LLM has full context.
        """
        uid = user_id or self._user_id
        ctx = self.context(task_description=task_description, user_id=uid)
        return self._render_system_prompt(ctx, task_description)

    # ------------------------------------------------------------------
    # Internal: render HyperContext as text
    # ------------------------------------------------------------------

    def _render_system_prompt(
        self, ctx: Dict[str, Any], task: Optional[str] = None,
    ) -> str:
        """Render HyperContext dict as a human-readable system prompt block."""
        parts = ["## Dhee Cognition Context"]

        if task:
            parts.append(f"\n**Current task:** {task}")

        # Performance
        perf = ctx.get("performance", [])
        if perf:
            parts.append("\n### Performance History")
            for p in perf:
                direction = "improving" if p.get("trend", 0) > 0 else "declining"
                parts.append(
                    f"- **{p['task_type']}**: avg={p['avg_score']:.2f}, "
                    f"trend={p['trend']:+.3f} ({direction}), "
                    f"attempts={p['total_attempts']}"
                )

        # Warnings
        warnings = ctx.get("warnings", [])
        if warnings:
            parts.append("\n### Warnings")
            for w in warnings:
                parts.append(f"- {w}")

        # Insights
        insights = ctx.get("insights", [])
        if insights:
            parts.append("\n### Insights from Past Work")
            for i in insights[:5]:
                parts.append(f"- [{i['type']}] {i['content']}")

        # Intentions
        intentions = ctx.get("intentions", [])
        if intentions:
            parts.append("\n### Triggered Reminders")
            for i in intentions:
                parts.append(f"- {i['description']}")

        # Contrasts (Phase 2)
        contrasts = ctx.get("contrasts", [])
        if contrasts:
            parts.append("\n### Contrastive Evidence (Do / Avoid)")
            for c in contrasts[:3]:
                parts.append(f"- **Do:** {c.get('do', '')[:150]}")
                parts.append(f"  **Avoid:** {c.get('avoid', '')[:150]}")

        # Heuristics (Phase 2)
        heuristics = ctx.get("heuristics", [])
        if heuristics:
            parts.append("\n### Learned Heuristics")
            for h in heuristics[:3]:
                parts.append(
                    f"- [{h.get('level', 'domain')}] {h.get('heuristic', '')[:200]}"
                )

        # Memories
        memories = ctx.get("memories", [])
        if memories:
            parts.append("\n### Relevant Memories")
            for m in memories[:5]:
                mem_text = m.get("memory", "")[:200]
                if mem_text:
                    parts.append(f"- {mem_text}")

        return "\n".join(parts)
