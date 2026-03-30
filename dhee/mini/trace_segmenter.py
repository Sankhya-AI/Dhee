"""Trace segmenter — converts agent trajectories into training spans.

Based on Structured Agent Distillation (Liu et al., arXiv:2505.13820):
segments agent interaction traces into [REASON], [ACT], and [MEMORY_OP]
spans with span-specific training losses for more efficient learning.

The key insight: different types of agent behavior (reasoning vs action
vs memory management) benefit from different training objectives.
Token-level distillation treats them uniformly and is less effective.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class SpanType(str, Enum):
    """Type of training span — each gets its own loss weight."""

    REASON = "reason"         # Internal reasoning, planning, analysis
    ACT = "act"               # Tool calls, commands, actions taken
    MEMORY_OP = "memory_op"   # Memory operations (store, retrieve, update, summarize, discard)
    REFLECT = "reflect"       # Self-reflection, insight synthesis
    OBSERVE = "observe"       # Observation, reading results, understanding state


@dataclass
class TrainingSpan:
    """A single segment of an agent trajectory for training.

    Each span has a type, the text content, and metadata about the
    trajectory it came from. Spans from successful trajectories are
    used for SFT; paired success/failure spans for DPO.
    """
    id: str
    span_type: SpanType
    content: str                 # the text of this span
    context_before: str          # preceding context (for input)
    trajectory_id: str
    step_index: int
    task_description: str
    success: bool                # was the overall trajectory successful?
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_sft_example(self) -> Dict[str, str]:
        """Format as SFT training example (input → output)."""
        return {
            "input": f"[TASK] {self.task_description}\n[CONTEXT] {self.context_before}",
            "output": f"[{self.span_type.value.upper()}] {self.content}",
            "type": self.span_type.value,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "span_type": self.span_type.value,
            "content": self.content,
            "context_before": self.context_before[:500],
            "trajectory_id": self.trajectory_id,
            "step_index": self.step_index,
            "task_description": self.task_description,
            "success": self.success,
        }


# Patterns for classifying steps into span types
_MEMORY_PATTERNS = re.compile(
    r"(?:remember|recall|search|store|forget|update.*memor|delete.*memor|checkpoint)",
    re.IGNORECASE,
)
_REASON_PATTERNS = re.compile(
    r"(?:think|plan|analyze|consider|reason|decide|evaluate|assess|compare)",
    re.IGNORECASE,
)
_REFLECT_PATTERNS = re.compile(
    r"(?:reflect|insight|learned|worked|failed|improve|heuristic|takeaway)",
    re.IGNORECASE,
)


class TraceSegmenter:
    """Segments agent trajectories into typed training spans.

    Takes a Trajectory (from dhee.skills.trajectory) and produces
    a list of TrainingSpan objects suitable for:
      - SFT: train on successful spans
      - DPO: pair successful/failed spans for preference learning
      - RL: use retrieval quality as reward signal

    Usage:
        from dhee.mini.trace_segmenter import TraceSegmenter
        from dhee.skills.schema import Trajectory

        segmenter = TraceSegmenter()
        spans = segmenter.segment(trajectory)

        # For SFT training
        sft_data = segmenter.format_for_sft(spans)

        # For DPO training
        dpo_data = segmenter.format_for_dpo(success_spans, failure_spans)
    """

    def segment(self, trajectory) -> List[TrainingSpan]:
        """Segment a trajectory into typed training spans.

        Args:
            trajectory: A Trajectory object from dhee.skills.schema

        Returns:
            List of TrainingSpan objects
        """
        spans: List[TrainingSpan] = []
        context_parts: List[str] = []

        for i, step in enumerate(trajectory.steps):
            # Classify the step
            span_type = self._classify_step(step)

            # Build content from step
            content = self._extract_content(step)
            if not content:
                continue

            # Context is everything before this step
            context_before = "\n".join(context_parts[-3:])  # last 3 steps

            span = TrainingSpan(
                id=str(uuid.uuid4()),
                span_type=span_type,
                content=content,
                context_before=context_before,
                trajectory_id=trajectory.id,
                step_index=i,
                task_description=trajectory.task_description,
                success=trajectory.success,
                metadata={
                    "tool": getattr(step, "tool", ""),
                    "error": getattr(step, "error", None),
                    "duration_ms": getattr(step, "duration_ms", None),
                },
            )
            spans.append(span)

            # Update rolling context
            context_parts.append(f"[{span_type.value}] {content[:200]}")

        return spans

    def format_for_sft(self, spans: List[TrainingSpan]) -> List[Dict[str, str]]:
        """Format successful spans as SFT training examples."""
        return [
            span.to_sft_example()
            for span in spans
            if span.success
        ]

    def format_for_dpo(
        self,
        success_spans: List[TrainingSpan],
        failure_spans: List[TrainingSpan],
    ) -> List[Dict[str, Any]]:
        """Create DPO training pairs from success/failure spans.

        Pairs are created by matching spans with the same span_type
        and similar step_index from successful and failed trajectories.
        """
        pairs = []

        # Group by span type
        success_by_type: Dict[str, List[TrainingSpan]] = {}
        failure_by_type: Dict[str, List[TrainingSpan]] = {}

        for s in success_spans:
            success_by_type.setdefault(s.span_type.value, []).append(s)
        for f in failure_spans:
            failure_by_type.setdefault(f.span_type.value, []).append(f)

        # Create pairs for each shared type
        for span_type in set(success_by_type) & set(failure_by_type):
            chosen_list = success_by_type[span_type]
            rejected_list = failure_by_type[span_type]

            # Pair by position (zip truncates to shorter)
            for chosen, rejected in zip(chosen_list, rejected_list):
                pairs.append({
                    "prompt": f"[TASK] {chosen.task_description}\n"
                              f"[CONTEXT] {chosen.context_before}",
                    "chosen": f"[{span_type.upper()}] {chosen.content}",
                    "rejected": f"[{span_type.upper()}] {rejected.content}",
                    "span_type": span_type,
                })

        return pairs

    def _classify_step(self, step) -> SpanType:
        """Classify a trajectory step into a span type."""
        action = getattr(step, "action", "")
        tool = getattr(step, "tool", "")
        result_summary = getattr(step, "result_summary", "")
        combined = f"{action} {tool} {result_summary}"

        # Memory operations
        if _MEMORY_PATTERNS.search(combined):
            return SpanType.MEMORY_OP

        # Reflection
        if _REFLECT_PATTERNS.search(combined):
            return SpanType.REFLECT

        # Reasoning (no tool call, just thinking)
        if not tool and _REASON_PATTERNS.search(combined):
            return SpanType.REASON

        # Tool call = action
        if tool:
            return SpanType.ACT

        # Observation (reading results)
        if result_summary and not tool:
            return SpanType.OBSERVE

        # Default to reasoning
        return SpanType.REASON

    def _extract_content(self, step) -> str:
        """Extract the text content from a trajectory step."""
        parts = []
        action = getattr(step, "action", "")
        tool = getattr(step, "tool", "")
        result_summary = getattr(step, "result_summary", "")

        if action:
            parts.append(action)
        if tool:
            args = getattr(step, "args", {})
            args_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3]) if args else ""
            parts.append(f"tool={tool}({args_str})")
        if result_summary:
            parts.append(f"→ {result_summary[:300]}")

        return " | ".join(parts)
