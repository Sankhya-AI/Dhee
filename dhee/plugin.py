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

from dhee.checkpoint_runtime import run_checkpoint_common

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

        # Build the CognitionKernel (state) + Buddhi (intelligence) layers
        from dhee.core.cognition_kernel import CognitionKernel
        from dhee.core.buddhi import Buddhi
        buddhi_dir = str(self._engram.data_dir / "buddhi")
        self._kernel = CognitionKernel(data_dir=buddhi_dir)
        self._buddhi = Buddhi(data_dir=buddhi_dir, kernel=self._kernel)

        # Passive session tracker — auto-context + auto-checkpoint
        from dhee.core.session_tracker import SessionTracker
        self._tracker = SessionTracker()

        # Hook registry for harness integration
        self._hooks: Dict[str, List[Callable]] = self._init_hooks()

        # Session tracking (kept for backward compat with session_start/session_end)
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

    @property
    def kernel(self):
        """Access the CognitionKernel for direct state manipulation."""
        return self._kernel

    @property
    def memory(self):
        """Expose the configured runtime memory engine for advanced integrations."""
        return self._engram.memory

    # ------------------------------------------------------------------
    # Hook registry
    # ------------------------------------------------------------------

    _HOOK_EVENTS = frozenset([
        "pre_remember", "post_remember",
        "pre_recall", "post_recall",
        "pre_context", "post_context",
        "pre_checkpoint", "post_checkpoint",
    ])

    def _init_hooks(self) -> Dict[str, List[Callable]]:
        """Create a fresh hook registry."""
        return {event: [] for event in self._HOOK_EVENTS}

    def register_hook(self, event: str, callback: Callable) -> None:
        """Register a callback for a lifecycle event.

        Events: pre_remember, post_remember, pre_recall, post_recall,
                pre_context, post_context, pre_checkpoint, post_checkpoint.

        Pre-hooks receive the arguments dict. Post-hooks receive the result dict.
        """
        if event not in self._hooks:
            raise ValueError(f"Unknown hook event: {event}. Valid: {list(self._hooks.keys())}")
        self._hooks[event].append(callback)

    def _fire_hooks(self, event: str, data: Any) -> None:
        """Fire all registered hooks for an event."""
        for callback in self._hooks.get(event, []):
            try:
                callback(data)
            except Exception:
                logger.debug("Hook %s failed", event)

    @staticmethod
    def _health_error(component: str, exc: Exception) -> Dict[str, str]:
        return {
            "component": component,
            "error": f"{type(exc).__name__}: {exc}",
        }

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
        self._fire_hooks("pre_remember", {"content": content, "user_id": uid, "metadata": metadata})

        # Auto-tier memory content
        from dhee.core.session_tracker import classify_tier
        tier = classify_tier(content)
        meta = dict(metadata) if metadata else {}
        if tier != "smriti":
            meta["tier"] = tier

        result = self._engram.add(content, user_id=uid, infer=False, metadata=meta or None)

        response: Dict[str, Any] = {"stored": True}
        memory_id = None
        if isinstance(result, dict):
            rs = result.get("results", [])
            if rs:
                memory_id = rs[0].get("id")
                response["id"] = memory_id
        if tier == "shruti":
            response["tier"] = "shruti"

        # Session tracking — may trigger auto-context
        signals = self._tracker.on_remember(content, memory_id)
        self._handle_tracker_signals(signals, uid)

        # Buddhi: detect intentions, record episode event, create beliefs
        intention = self._buddhi.on_memory_stored(
            content=content, user_id=uid, memory_id=memory_id,
        )
        if intention:
            response["detected_intention"] = intention.to_dict()

        self._fire_hooks("post_remember", response)
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
        self._fire_hooks("pre_recall", {"query": query, "user_id": uid, "limit": limit})
        results = self._engram.search(query, user_id=uid, limit=limit)
        formatted = [
            {
                "memory": r.get("memory", r.get("content", "")),
                "score": round(r.get("composite_score", r.get("score", 0.0)), 3),
                "id": r.get("id", ""),
            }
            for r in results
        ]

        # Session tracking
        signals = self._tracker.on_recall(query, formatted)
        self._handle_tracker_signals(signals, uid)

        self._fire_hooks("post_recall", formatted)
        return formatted

    # ------------------------------------------------------------------
    # Tool 3: context
    # ------------------------------------------------------------------

    def context(
        self,
        task_description: Optional[str] = None,
        user_id: Optional[str] = None,
        operational: bool = False,
    ) -> Dict[str, Any]:
        """HyperAgent session bootstrap. Returns everything the agent needs.

        Args:
            operational: If True, return compact actionable-only format.
        """
        uid = user_id or self._user_id
        self._fire_hooks("pre_context", {
            "task_description": task_description, "user_id": uid, "operational": operational,
        })
        self._tracker.on_context(task_description)
        hyper_ctx = self._buddhi.get_hyper_context(
            user_id=uid,
            task_description=task_description,
            memory=self.memory,
        )
        if operational:
            result = hyper_ctx.to_operational_dict()
        else:
            result = hyper_ctx.to_dict()
        self._fire_hooks("post_context", result)
        return result

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
        # Structured task state (Phase 3)
        goal: Optional[str] = None,
        plan: Optional[List[str]] = None,
        plan_rationale: Optional[str] = None,
        blockers: Optional[List[str]] = None,
        outcome_evidence: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Save session state. Where the cognition happens.

        1. Session digest → cross-agent handoff
        2. Batch enrichment → 1 LLM call per ~10 memories
        3. Outcome recording → performance tracking
        4. Insight synthesis → transferable learnings
        5. Intention storage → prospective memory
        6. Episode closure → temporal experience unit
        7. Task state update → structured progress tracking
        8. Selective forgetting → utility-based cleanup
        """
        uid = user_id or self._user_id
        self._fire_hooks("pre_checkpoint", {
            "summary": summary, "user_id": uid, "task_type": task_type,
            "outcome_score": outcome_score, "status": status,
        })
        self._tracker.on_checkpoint()

        # Auto-fill task_type if not provided
        if not task_type:
            task_type = self._tracker.get_inferred_task_type()
            if task_type == "general":
                task_type = None

        # Auto-fill outcome if not provided
        if outcome_score is None and self._tracker.op_count >= 3:
            outcome = self._tracker.get_outcome_signals()
            outcome_score = outcome.get("outcome_score")
            if not what_worked:
                what_worked = outcome.get("what_worked")

        result = run_checkpoint_common(
            logger=logger,
            log_prefix="Plugin checkpoint",
            user_id=uid,
            summary=summary,
            status=status,
            agent_id=agent_id,
            repo=repo,
            decisions=decisions,
            files_touched=files_touched,
            todos=todos,
            task_type=task_type,
            outcome_score=outcome_score,
            what_worked=what_worked,
            what_failed=what_failed,
            key_decision=key_decision,
            remember_to=remember_to,
            trigger_keywords=trigger_keywords,
            enrich_pending_fn=self._engram.enrich_pending,
            record_outcome_fn=self._buddhi.record_outcome,
            reflect_fn=self._buddhi.reflect,
            store_intention_fn=self._buddhi.store_intention,
        )

        # 6. Episode closure (via kernel)
        ep_result = self._kernel.record_checkpoint_event(
            uid, summary, status, outcome_score,
        )
        result.update(ep_result)

        # 7. Task state update (via kernel)
        if goal or plan or blockers:
            task_result = self._kernel.update_task_on_checkpoint(
                user_id=uid,
                goal=goal,
                plan=plan,
                plan_rationale=plan_rationale,
                blockers=blockers,
                task_type=task_type or "general",
                status=status,
                outcome_score=outcome_score,
                outcome_evidence=outcome_evidence,
                summary=summary,
            )
            result.update(task_result)

        # 8. Selective forgetting (via kernel)
        forget_result = self._kernel.selective_forget(uid)
        result.update(forget_result)

        self._fire_hooks("post_checkpoint", result)
        return result

    # ------------------------------------------------------------------
    # Session lifecycle (Hermes-style frozen snapshot)
    # ------------------------------------------------------------------

    def session_start(
        self,
        task_description: Optional[str] = None,
        user_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> str:
        """Start a session and return a frozen system prompt block.

        The system prompt contains the full HyperContext rendered as text.
        Inject it into your agent's system prompt at session start.
        The snapshot is frozen — writes during the session update storage
        but don't change this prompt, preserving LLM prefix caches.

        Also begins an Episode and creates/resumes a TaskState.
        """
        uid = user_id or self._user_id
        self._session_id = str(uuid.uuid4())
        self._session_start_time = time.time()

        # Begin episode (via kernel)
        try:
            self._kernel.episodes.begin_episode(
                user_id=uid,
                task_description=task_description or "session",
                task_type=task_type or "general",
            )
        except Exception as exc:
            logger.warning("Session start episode initialization failed: %s", exc, exc_info=True)

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
    # Auto-lifecycle (driven by SessionTracker)
    # ------------------------------------------------------------------

    def _handle_tracker_signals(self, signals: Dict[str, Any], user_id: str) -> None:
        """Process signals from the session tracker."""
        if not signals:
            return

        # Auto-checkpoint a timed-out previous session
        if signals.get("needs_auto_checkpoint"):
            args = signals.get("auto_checkpoint_args", {})
            try:
                checkpoint_result = self.checkpoint(user_id=user_id, **args)
                for warning in checkpoint_result.get("warnings", []):
                    logger.warning("Plugin auto-checkpoint warning: %s", warning)
            except Exception as exc:
                logger.warning("Plugin auto-checkpoint failed: %s", exc, exc_info=True)

        # Auto-context for new session
        if signals.get("needs_auto_context"):
            task = signals.get("inferred_task")
            try:
                self.context(task_description=task, user_id=user_id)
            except Exception as exc:
                logger.warning("Plugin auto-context failed: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Cognition health (harness monitoring)
    # ------------------------------------------------------------------

    def cognition_health(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Health status of all cognitive subsystems.

        Returns counts, utility stats, and degradation warnings.
        Useful for harness dashboards and monitoring. Zero LLM calls.
        """
        uid = user_id or self._user_id
        health: Dict[str, Any] = {}
        errors: List[Dict[str, str]] = []

        health["kernel"] = self._kernel.get_stats()
        health["buddhi"] = self._buddhi.get_stats()

        warnings: List[str] = []
        try:
            policies = self._kernel.policies.get_user_policies(uid)
            low_util = [p for p in policies if p.utility < -0.2 and p.apply_count >= 3]
            if low_util:
                warnings.append(f"{len(low_util)} policies with negative utility")
        except Exception as exc:
            logger.warning(
                "Cognition health derivation failed for policies: %s",
                exc,
                exc_info=True,
            )
            errors.append(self._health_error("policies.get_user_policies", exc))

        try:
            active_intentions = self._kernel.intentions.get_active(uid)
            if len(active_intentions) > 20:
                warnings.append(
                    f"{len(active_intentions)} active intentions (consider cleanup)"
                )
        except Exception as exc:
            logger.warning(
                "Cognition health derivation failed for intentions: %s",
                exc,
                exc_info=True,
            )
            errors.append(self._health_error("intentions.get_active", exc))

        try:
            contradictions = self._kernel.beliefs.get_contradictions(uid)
            if len(contradictions) > 5:
                warnings.append(
                    f"{len(contradictions)} unresolved belief contradictions"
                )
        except Exception as exc:
            logger.warning(
                "Cognition health derivation failed for contradictions: %s",
                exc,
                exc_info=True,
            )
            errors.append(self._health_error("beliefs.get_contradictions", exc))

        health["warnings"] = warnings
        if errors:
            health["errors"] = errors
        return health

    # ------------------------------------------------------------------
    # Phase 3: Belief management
    # ------------------------------------------------------------------

    def add_belief(
        self,
        claim: str,
        domain: str = "general",
        confidence: float = 0.5,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicitly add a belief with confidence tracking."""
        uid = user_id or self._user_id
        belief, contradictions = self._kernel.beliefs.add_belief(
            user_id=uid, claim=claim, domain=domain,
            confidence=confidence, source="user",
        )
        try:
            self._kernel.beliefs.record_influence(
                belief.id,
                user_id=uid,
                influence_type="activated",
                query=claim,
                metadata={"action": "add_belief"},
            )
        except Exception:
            logger.debug("Failed to record explicit belief activation", exc_info=True)
        result = {"belief_id": belief.id, "confidence": belief.confidence}
        if contradictions:
            result["contradictions"] = [
                {"claim": c.claim[:200], "confidence": c.confidence}
                for c in contradictions
            ]
        return result

    def challenge_belief(
        self,
        belief_id: str,
        evidence: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Present contradicting evidence to a belief."""
        belief = self._kernel.beliefs.challenge_belief(belief_id, evidence)
        if belief:
            try:
                self._kernel.beliefs.record_influence(
                    belief.id,
                    user_id=user_id or self._user_id,
                    influence_type="activated",
                    query=evidence,
                    metadata={"action": "challenge_belief"},
                )
            except Exception:
                logger.debug("Failed to record explicit belief challenge", exc_info=True)
            return belief.to_compact()
        return None

    # ------------------------------------------------------------------
    # Phase 3: Task state management
    # ------------------------------------------------------------------

    def create_task(
        self,
        goal: str,
        task_type: str = "general",
        plan: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a structured task with optional plan."""
        uid = user_id or self._user_id
        task = self._kernel.tasks.create_task(
            user_id=uid, goal=goal, task_type=task_type, plan=plan,
        )
        task.start()
        self._kernel.tasks.update_task(task)
        return task.to_compact()

    def advance_task(
        self,
        note: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Advance the active task to the next step."""
        uid = user_id or self._user_id
        task = self._kernel.tasks.get_active_task(uid)
        if not task:
            return None
        task.advance_step(note)
        self._kernel.tasks.update_task(task)
        return task.to_compact()

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
            store = TrajectoryStore(memory=self.memory)
            store.save(trajectory)
            result["stored"] = True
        except Exception as exc:
            logger.warning("Trajectory persistence failed: %s", exc, exc_info=True)
            result["stored"] = False
            result["storage_error"] = str(exc)

        return result

    def close(self) -> None:
        """Flush cognition state and release runtime resources."""
        errors: List[str] = []

        try:
            self._buddhi.flush()
        except Exception as exc:
            logger.exception("DheePlugin close failed for buddhi.flush")
            errors.append(f"buddhi.flush: {type(exc).__name__}: {exc}")

        try:
            self._engram.close()
        except Exception as exc:
            logger.exception("DheePlugin close failed for engram.close")
            errors.append(f"engram.close: {type(exc).__name__}: {exc}")

        if errors:
            raise RuntimeError(
                "Failed to close DheePlugin resources: " + "; ".join(errors)
            )

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

        # Policies (Phase 3)
        policies = ctx.get("policies", [])
        if policies:
            parts.append("\n### Proven Strategies")
            for p in policies[:3]:
                parts.append(
                    f"- **{p.get('name', 'policy')}** (win rate: {p.get('win_rate', 0):.0%}): "
                    f"{p.get('do', '')[:150]}"
                )
                avoid = p.get("avoid", [])
                if avoid:
                    parts.append(f"  Avoid: {', '.join(avoid[:3])}")

        # Beliefs (Phase 3)
        beliefs = ctx.get("beliefs", [])
        if beliefs:
            challenged = [b for b in beliefs if b.get("has_contradictions")]
            confident = [b for b in beliefs if not b.get("has_contradictions") and b.get("confidence", 0) >= 0.7]
            if confident:
                parts.append("\n### Established Beliefs")
                for b in confident[:5]:
                    parts.append(f"- {b['claim']} (confidence: {b['confidence']:.0%})")
            if challenged:
                parts.append("\n### Beliefs Under Review")
                for b in challenged[:3]:
                    parts.append(f"- {b['claim']} (confidence: {b['confidence']:.0%}, contradicted)")

        # Task State (Phase 3)
        task_states = ctx.get("task_states", [])
        if task_states:
            active = [t for t in task_states if t.get("status") in ("in_progress", "blocked")]
            if active:
                parts.append("\n### Active Tasks")
                for t in active[:2]:
                    parts.append(
                        f"- **{t['goal'][:100]}** ({t['status']}, "
                        f"progress: {t.get('progress', 0):.0%})"
                    )
                    if t.get("current_step"):
                        parts.append(f"  Current step: {t['current_step'][:100]}")
                    if t.get("blockers"):
                        parts.append(f"  Blockers: {', '.join(t['blockers'][:2])}")

        # Episodes (Phase 3)
        episodes = ctx.get("episodes", [])
        if episodes:
            relevant = [e for e in episodes if e.get("outcome") is not None]
            if relevant:
                parts.append("\n### Recent Experience")
                for e in relevant[:3]:
                    outcome_str = f"score={e['outcome']:.2f}" if e['outcome'] is not None else "no outcome"
                    parts.append(
                        f"- {e.get('task', '')[:100]} ({outcome_str}, "
                        f"{e.get('events', 0)} events, {e.get('duration_min', 0):.0f}min)"
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
