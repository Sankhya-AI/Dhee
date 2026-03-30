"""Trigger — confidence-scored, temporal, and composite trigger system.

Replaces the simple keyword-only trigger matching in Buddhi.Intention.

A Trigger defines WHEN something should happen, with:
  - Confidence: how likely is this trigger match (0-1), not just boolean
  - Temporal: recurring schedules, delay-after-event, deadline windows
  - Composite: AND/OR/NOT composition of sub-triggers
  - Context matching: semantic keyword overlap, not just exact match

Trigger types:
  - KeywordTrigger: fires when keywords match in context (with confidence)
  - TimeTrigger: fires at/after a specific time, or on recurring schedule
  - EventTrigger: fires when a specific event type occurs
  - CompositeTrigger: AND/OR/NOT composition of sub-triggers
  - SequenceTrigger: fires when events happen in order within time window

Each trigger produces a TriggerResult with:
  - fired: bool (did it fire?)
  - confidence: float (how confident in the match, 0-1)
  - reason: str (why it fired, for debugging)
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class TriggerResult:
    """Result of evaluating a trigger."""
    fired: bool
    confidence: float       # 0.0-1.0
    reason: str
    trigger_id: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fired": self.fired,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "trigger_id": self.trigger_id,
            "timestamp": self.timestamp,
        }


class TriggerBase(ABC):
    """Abstract base for all trigger types."""

    def __init__(self, trigger_id: str = "", min_confidence: float = 0.3):
        self.trigger_id = trigger_id
        self.min_confidence = min_confidence

    @abstractmethod
    def evaluate(self, context: TriggerContext) -> TriggerResult:
        """Evaluate this trigger against the given context."""
        ...

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        ...

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TriggerBase:
        """Factory: reconstruct trigger from dict based on 'type' field."""
        trigger_type = d.get("type", "keyword")
        constructors = {
            "keyword": KeywordTrigger,
            "time": TimeTrigger,
            "event": EventTrigger,
            "composite": CompositeTrigger,
            "sequence": SequenceTrigger,
        }
        constructor = constructors.get(trigger_type)
        if not constructor:
            raise ValueError(f"Unknown trigger type: {trigger_type}")
        return constructor._from_dict(d)


@dataclass
class TriggerContext:
    """The context against which triggers are evaluated."""
    text: str = ""                          # current query/content text
    event_type: Optional[str] = None        # "memory_add", "search", "checkpoint", etc.
    timestamp: float = 0.0                  # current time
    metadata: Dict[str, Any] = field(default_factory=dict)
    recent_events: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# ---------------------------------------------------------------------------
# Keyword Trigger — fires on context keyword overlap with confidence
# ---------------------------------------------------------------------------

class KeywordTrigger(TriggerBase):
    """Fires when keywords match in context, with confidence scoring.

    Confidence = matched_keywords / total_keywords * keyword_weight_sum
    Supports required keywords (must match) and optional keywords (boost).
    """

    def __init__(
        self,
        keywords: List[str],
        required_keywords: Optional[List[str]] = None,
        trigger_id: str = "",
        min_confidence: float = 0.3,
    ):
        super().__init__(trigger_id, min_confidence)
        self.keywords = [k.lower() for k in keywords]
        self.required_keywords = [k.lower() for k in (required_keywords or [])]

    def evaluate(self, context: TriggerContext) -> TriggerResult:
        text_lower = context.text.lower()
        text_words = set(text_lower.split())

        # Check required keywords first
        for rk in self.required_keywords:
            if rk not in text_lower:
                return TriggerResult(
                    fired=False, confidence=0.0,
                    reason=f"Required keyword '{rk}' not found",
                    trigger_id=self.trigger_id,
                )

        # Score optional keywords
        if not self.keywords:
            confidence = 1.0 if not self.required_keywords else 1.0
        else:
            matched = sum(
                1 for kw in self.keywords
                if kw in text_lower or kw in text_words
            )
            confidence = matched / len(self.keywords)

        # Boost for required keyword match
        if self.required_keywords:
            confidence = min(1.0, confidence + 0.3)

        fired = confidence >= self.min_confidence
        matched_list = [kw for kw in self.keywords if kw in text_lower]

        return TriggerResult(
            fired=fired,
            confidence=confidence,
            reason=f"Keywords matched: {matched_list}" if fired else "Insufficient keyword match",
            trigger_id=self.trigger_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "keyword",
            "trigger_id": self.trigger_id,
            "keywords": self.keywords,
            "required_keywords": self.required_keywords,
            "min_confidence": self.min_confidence,
        }

    @classmethod
    def _from_dict(cls, d: Dict[str, Any]) -> KeywordTrigger:
        return cls(
            keywords=d.get("keywords", []),
            required_keywords=d.get("required_keywords"),
            trigger_id=d.get("trigger_id", ""),
            min_confidence=d.get("min_confidence", 0.3),
        )


# ---------------------------------------------------------------------------
# Time Trigger — fires at/after a time, or on recurring schedule
# ---------------------------------------------------------------------------

class TimeTrigger(TriggerBase):
    """Fires based on time conditions.

    Modes:
      - after: fires once after a specific timestamp
      - before: fires if checked before a deadline (urgency increases as deadline approaches)
      - recurring: fires every interval_seconds (resets after firing)
      - window: fires during a specific time window [start, end]
    """

    def __init__(
        self,
        mode: str = "after",   # "after" | "before" | "recurring" | "window"
        target_time: Optional[float] = None,
        interval_seconds: Optional[float] = None,
        window_start: Optional[float] = None,
        window_end: Optional[float] = None,
        last_fired: Optional[float] = None,
        trigger_id: str = "",
        min_confidence: float = 0.3,
    ):
        super().__init__(trigger_id, min_confidence)
        self.mode = mode
        self.target_time = target_time
        self.interval_seconds = interval_seconds
        self.window_start = window_start
        self.window_end = window_end
        self.last_fired = last_fired

    def evaluate(self, context: TriggerContext) -> TriggerResult:
        now = context.timestamp or time.time()

        if self.mode == "after":
            if self.target_time and now >= self.target_time:
                # Confidence increases with time past deadline
                overdue_hours = (now - self.target_time) / 3600
                confidence = min(1.0, 0.7 + 0.1 * overdue_hours)
                self.last_fired = now
                return TriggerResult(
                    fired=True, confidence=confidence,
                    reason=f"Time trigger: {overdue_hours:.1f}h past target",
                    trigger_id=self.trigger_id,
                )
            return TriggerResult(
                fired=False, confidence=0.0,
                reason="Target time not yet reached",
                trigger_id=self.trigger_id,
            )

        elif self.mode == "before":
            if self.target_time and now < self.target_time:
                # Urgency increases as deadline approaches
                remaining_hours = (self.target_time - now) / 3600
                if remaining_hours < 24:
                    confidence = min(1.0, 1.0 - remaining_hours / 24)
                    return TriggerResult(
                        fired=confidence >= self.min_confidence,
                        confidence=confidence,
                        reason=f"Deadline in {remaining_hours:.1f}h",
                        trigger_id=self.trigger_id,
                    )
            return TriggerResult(
                fired=False, confidence=0.0,
                reason="Not within deadline window",
                trigger_id=self.trigger_id,
            )

        elif self.mode == "recurring":
            if self.interval_seconds:
                if self.last_fired is None or (now - self.last_fired) >= self.interval_seconds:
                    self.last_fired = now
                    return TriggerResult(
                        fired=True, confidence=0.8,
                        reason=f"Recurring trigger (every {self.interval_seconds}s)",
                        trigger_id=self.trigger_id,
                    )
            return TriggerResult(
                fired=False, confidence=0.0,
                reason="Recurring interval not elapsed",
                trigger_id=self.trigger_id,
            )

        elif self.mode == "window":
            if self.window_start and self.window_end:
                if self.window_start <= now <= self.window_end:
                    # Confidence peaks at window center
                    duration = self.window_end - self.window_start
                    center = self.window_start + duration / 2
                    distance_from_center = abs(now - center) / (duration / 2)
                    confidence = max(0.5, 1.0 - 0.5 * distance_from_center)
                    return TriggerResult(
                        fired=True, confidence=confidence,
                        reason="Within time window",
                        trigger_id=self.trigger_id,
                    )
            return TriggerResult(
                fired=False, confidence=0.0,
                reason="Outside time window",
                trigger_id=self.trigger_id,
            )

        return TriggerResult(
            fired=False, confidence=0.0,
            reason=f"Unknown time mode: {self.mode}",
            trigger_id=self.trigger_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "time",
            "trigger_id": self.trigger_id,
            "mode": self.mode,
            "target_time": self.target_time,
            "interval_seconds": self.interval_seconds,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "last_fired": self.last_fired,
            "min_confidence": self.min_confidence,
        }

    @classmethod
    def _from_dict(cls, d: Dict[str, Any]) -> TimeTrigger:
        return cls(
            mode=d.get("mode", "after"),
            target_time=d.get("target_time"),
            interval_seconds=d.get("interval_seconds"),
            window_start=d.get("window_start"),
            window_end=d.get("window_end"),
            last_fired=d.get("last_fired"),
            trigger_id=d.get("trigger_id", ""),
            min_confidence=d.get("min_confidence", 0.3),
        )


# ---------------------------------------------------------------------------
# Event Trigger — fires on specific event types
# ---------------------------------------------------------------------------

class EventTrigger(TriggerBase):
    """Fires when a specific event type occurs in context."""

    def __init__(
        self,
        event_types: List[str],
        content_pattern: Optional[str] = None,  # regex pattern on content
        trigger_id: str = "",
        min_confidence: float = 0.3,
    ):
        super().__init__(trigger_id, min_confidence)
        self.event_types = event_types
        self.content_pattern = content_pattern
        self._compiled_pattern = re.compile(content_pattern, re.IGNORECASE) if content_pattern else None

    def evaluate(self, context: TriggerContext) -> TriggerResult:
        if not context.event_type:
            return TriggerResult(
                fired=False, confidence=0.0,
                reason="No event type in context",
                trigger_id=self.trigger_id,
            )

        if context.event_type not in self.event_types:
            return TriggerResult(
                fired=False, confidence=0.0,
                reason=f"Event '{context.event_type}' not in {self.event_types}",
                trigger_id=self.trigger_id,
            )

        confidence = 0.8

        # Check content pattern if specified
        if self._compiled_pattern and context.text:
            if self._compiled_pattern.search(context.text):
                confidence = 1.0
            else:
                confidence = 0.4

        fired = confidence >= self.min_confidence
        return TriggerResult(
            fired=fired, confidence=confidence,
            reason=f"Event '{context.event_type}' matched" if fired else "Pattern not matched",
            trigger_id=self.trigger_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "event",
            "trigger_id": self.trigger_id,
            "event_types": self.event_types,
            "content_pattern": self.content_pattern,
            "min_confidence": self.min_confidence,
        }

    @classmethod
    def _from_dict(cls, d: Dict[str, Any]) -> EventTrigger:
        return cls(
            event_types=d.get("event_types", []),
            content_pattern=d.get("content_pattern"),
            trigger_id=d.get("trigger_id", ""),
            min_confidence=d.get("min_confidence", 0.3),
        )


# ---------------------------------------------------------------------------
# Composite Trigger — AND/OR/NOT composition
# ---------------------------------------------------------------------------

class CompositeOp(str, Enum):
    AND = "and"     # All sub-triggers must fire
    OR = "or"       # At least one sub-trigger must fire
    NOT = "not"     # Invert first sub-trigger


class CompositeTrigger(TriggerBase):
    """Composes multiple triggers with AND/OR/NOT logic.

    Confidence for AND = min of sub-confidences (weakest link)
    Confidence for OR = max of sub-confidences (strongest match)
    Confidence for NOT = 1 - first sub-confidence
    """

    def __init__(
        self,
        op: CompositeOp,
        triggers: List[TriggerBase],
        trigger_id: str = "",
        min_confidence: float = 0.3,
    ):
        super().__init__(trigger_id, min_confidence)
        self.op = op
        self.triggers = triggers

    def evaluate(self, context: TriggerContext) -> TriggerResult:
        if not self.triggers:
            return TriggerResult(
                fired=False, confidence=0.0,
                reason="No sub-triggers",
                trigger_id=self.trigger_id,
            )

        results = [t.evaluate(context) for t in self.triggers]

        if self.op == CompositeOp.AND:
            all_fired = all(r.fired for r in results)
            confidence = min(r.confidence for r in results) if all_fired else 0.0
            reasons = [r.reason for r in results if r.fired]
            return TriggerResult(
                fired=all_fired and confidence >= self.min_confidence,
                confidence=confidence,
                reason=f"AND({', '.join(reasons)})" if all_fired else "Not all sub-triggers fired",
                trigger_id=self.trigger_id,
            )

        elif self.op == CompositeOp.OR:
            any_fired = any(r.fired for r in results)
            confidence = max(r.confidence for r in results) if any_fired else 0.0
            best = max(results, key=lambda r: r.confidence) if results else None
            return TriggerResult(
                fired=any_fired and confidence >= self.min_confidence,
                confidence=confidence,
                reason=f"OR: {best.reason}" if best and any_fired else "No sub-triggers fired",
                trigger_id=self.trigger_id,
            )

        elif self.op == CompositeOp.NOT:
            first = results[0]
            inverted_confidence = 1.0 - first.confidence
            return TriggerResult(
                fired=not first.fired and inverted_confidence >= self.min_confidence,
                confidence=inverted_confidence,
                reason=f"NOT({first.reason})",
                trigger_id=self.trigger_id,
            )

        return TriggerResult(
            fired=False, confidence=0.0,
            reason=f"Unknown composite op: {self.op}",
            trigger_id=self.trigger_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "composite",
            "trigger_id": self.trigger_id,
            "op": self.op.value,
            "triggers": [t.to_dict() for t in self.triggers],
            "min_confidence": self.min_confidence,
        }

    @classmethod
    def _from_dict(cls, d: Dict[str, Any]) -> CompositeTrigger:
        sub_triggers = [TriggerBase.from_dict(td) for td in d.get("triggers", [])]
        return cls(
            op=CompositeOp(d.get("op", "and")),
            triggers=sub_triggers,
            trigger_id=d.get("trigger_id", ""),
            min_confidence=d.get("min_confidence", 0.3),
        )


# ---------------------------------------------------------------------------
# Sequence Trigger — ordered events within time window
# ---------------------------------------------------------------------------

class SequenceTrigger(TriggerBase):
    """Fires when events happen in a specific order within a time window.

    Example: "memory_add" followed by "search" followed by "checkpoint"
    within 300 seconds → trigger a reflection.
    """

    def __init__(
        self,
        event_sequence: List[str],
        window_seconds: float = 300,
        trigger_id: str = "",
        min_confidence: float = 0.3,
    ):
        super().__init__(trigger_id, min_confidence)
        self.event_sequence = event_sequence
        self.window_seconds = window_seconds

    def evaluate(self, context: TriggerContext) -> TriggerResult:
        if not context.recent_events or not self.event_sequence:
            return TriggerResult(
                fired=False, confidence=0.0,
                reason="No recent events or no sequence defined",
                trigger_id=self.trigger_id,
            )

        now = context.timestamp or time.time()
        cutoff = now - self.window_seconds

        # Filter to recent events within window
        recent = [
            e for e in context.recent_events
            if e.get("timestamp", 0) >= cutoff
        ]

        # Check if sequence exists in order
        seq_idx = 0
        matched_times = []
        for event in recent:
            if seq_idx < len(self.event_sequence):
                if event.get("event_type") == self.event_sequence[seq_idx]:
                    matched_times.append(event.get("timestamp", now))
                    seq_idx += 1

        if seq_idx >= len(self.event_sequence):
            # Full sequence matched
            # Confidence based on how tight the sequence was
            if len(matched_times) >= 2:
                span = matched_times[-1] - matched_times[0]
                tightness = max(0.5, 1.0 - span / self.window_seconds)
            else:
                tightness = 0.8
            return TriggerResult(
                fired=True, confidence=tightness,
                reason=f"Sequence {self.event_sequence} completed within window",
                trigger_id=self.trigger_id,
            )

        return TriggerResult(
            fired=False, confidence=0.0,
            reason=f"Sequence incomplete: matched {seq_idx}/{len(self.event_sequence)}",
            trigger_id=self.trigger_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "sequence",
            "trigger_id": self.trigger_id,
            "event_sequence": self.event_sequence,
            "window_seconds": self.window_seconds,
            "min_confidence": self.min_confidence,
        }

    @classmethod
    def _from_dict(cls, d: Dict[str, Any]) -> SequenceTrigger:
        return cls(
            event_sequence=d.get("event_sequence", []),
            window_seconds=d.get("window_seconds", 300),
            trigger_id=d.get("trigger_id", ""),
            min_confidence=d.get("min_confidence", 0.3),
        )


# ---------------------------------------------------------------------------
# Trigger Manager — evaluates all triggers for an intention
# ---------------------------------------------------------------------------

class TriggerManager:
    """Evaluates triggers for the intention system.

    Replaces the simple keyword matching in Buddhi._check_intentions()
    with confidence-scored, composable trigger evaluation.
    """

    @staticmethod
    def evaluate_triggers(
        triggers: List[TriggerBase],
        context: TriggerContext,
    ) -> List[TriggerResult]:
        """Evaluate all triggers against context, return those that fired."""
        fired = []
        for trigger in triggers:
            try:
                result = trigger.evaluate(context)
                if result.fired:
                    fired.append(result)
            except Exception as e:
                logger.debug("Trigger evaluation error for %s: %s", trigger.trigger_id, e)
        return fired

    @staticmethod
    def build_context(
        text: str = "",
        event_type: Optional[str] = None,
        recent_events: Optional[List[Dict]] = None,
        metadata: Optional[Dict] = None,
    ) -> TriggerContext:
        """Build a trigger context from available information."""
        return TriggerContext(
            text=text,
            event_type=event_type,
            timestamp=time.time(),
            metadata=metadata or {},
            recent_events=recent_events or [],
        )

    @staticmethod
    def from_intention_keywords(keywords: List[str], trigger_after: Optional[str] = None) -> List[TriggerBase]:
        """Convert legacy Intention trigger_keywords/trigger_after to new triggers.

        Backwards-compatible bridge from old Intention format.
        """
        triggers: List[TriggerBase] = []

        if keywords:
            triggers.append(KeywordTrigger(
                keywords=keywords,
                trigger_id="keyword_legacy",
                min_confidence=0.3,
            ))

        if trigger_after:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(trigger_after)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                triggers.append(TimeTrigger(
                    mode="after",
                    target_time=dt.timestamp(),
                    trigger_id="time_legacy",
                    min_confidence=0.3,
                ))
            except (ValueError, TypeError):
                pass

        return triggers
