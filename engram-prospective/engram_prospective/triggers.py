"""Pure trigger evaluation functions for prospective memory.

Stateless functions that evaluate whether an intention's trigger condition
has been met. Called by Prospective.check_triggers() and heartbeat behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from engram_prospective.config import ProspectiveConfig


def evaluate_trigger(
    intention: Dict[str, Any],
    current_time: Optional[datetime] = None,
    events: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    config: Optional[ProspectiveConfig] = None,
) -> bool:
    """Evaluate whether an intention's trigger condition is met.

    Args:
        intention: The intention record (metadata dict from memory).
        current_time: Current time (defaults to now UTC).
        events: Dict of recent events for event-trigger matching.
        context: Dict of current context for condition-trigger matching.
        config: ProspectiveConfig for tolerance settings.

    Returns:
        True if the trigger condition is satisfied.
    """
    metadata = intention.get("metadata", {}) or intention
    trigger_type = metadata.get("pm_trigger_type", "")
    trigger_value = metadata.get("pm_trigger_value", "")

    if trigger_type == "time":
        return _evaluate_time_trigger(trigger_value, current_time, config)
    elif trigger_type == "event":
        return _evaluate_event_trigger(trigger_value, events)
    elif trigger_type == "condition":
        return _evaluate_condition_trigger(trigger_value, context)

    return False


def _evaluate_time_trigger(
    trigger_value: str,
    current_time: Optional[datetime] = None,
    config: Optional[ProspectiveConfig] = None,
) -> bool:
    """Check if a time-based trigger is due."""
    if not trigger_value:
        return False

    now = current_time or datetime.now(timezone.utc)
    cfg = config or ProspectiveConfig()

    try:
        trigger_time = datetime.fromisoformat(trigger_value.replace("Z", "+00:00"))
        # Trigger fires if current time is past the trigger time
        # (within tolerance window — we don't miss triggers that are slightly past)
        diff_seconds = (now - trigger_time).total_seconds()
        return diff_seconds >= -cfg.time_tolerance_seconds
    except (ValueError, TypeError):
        return False


def _evaluate_event_trigger(
    trigger_value: str,
    events: Optional[Dict[str, Any]] = None,
) -> bool:
    """Check if a named event has occurred."""
    if not trigger_value or not events:
        return False

    # Simple event name matching
    return trigger_value in events


def _evaluate_condition_trigger(
    trigger_value: str,
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Check if a condition is satisfied in the current context.

    Condition format: "key=value" or "key" (truthy check).
    """
    if not trigger_value or not context:
        return False

    if "=" in trigger_value:
        key, _, expected = trigger_value.partition("=")
        key = key.strip()
        expected = expected.strip()
        actual = str(context.get(key, "")).strip()
        return actual == expected
    else:
        # Truthy check
        return bool(context.get(trigger_value.strip()))


def is_expired(
    intention: Dict[str, Any],
    config: Optional[ProspectiveConfig] = None,
) -> bool:
    """Check if an intention has passed its expiry date."""
    metadata = intention.get("metadata", {}) or intention
    expiry = metadata.get("pm_expiry")
    if not expiry:
        return False

    try:
        expiry_time = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > expiry_time
    except (ValueError, TypeError):
        return False
