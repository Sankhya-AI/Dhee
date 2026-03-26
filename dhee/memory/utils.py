from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dhee.exceptions import FadeMemValidationError


def normalize_messages(messages: Any) -> List[Dict[str, Any]]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    if isinstance(messages, dict):
        return [messages]
    if isinstance(messages, list):
        return messages
    raise FadeMemValidationError(
        "messages must be str, dict, or list[dict]",
        error_code="VALIDATION_003",
        details={"provided_type": type(messages).__name__},
        suggestion="Convert your input to a string, dictionary, or list of dictionaries.",
    )


def parse_messages(messages: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"system: {content}")
        elif role == "assistant":
            parts.append(f"assistant: {content}")
        else:
            parts.append(f"user: {content}")
    return "\n".join(parts)


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`")
        # Remove possible language tag on the first line
        lines = text.splitlines()
        if lines and lines[0].startswith("json"):
            lines = lines[1:]
        text = "\n".join(lines)
    return text.strip()


def build_filters_and_metadata(
    *,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    input_metadata: Optional[Dict[str, Any]] = None,
    input_filters: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    base_metadata = deepcopy(input_metadata) if input_metadata else {}
    effective_filters = deepcopy(input_filters) if input_filters else {}

    session_ids = []
    if user_id:
        base_metadata["user_id"] = user_id
        effective_filters["user_id"] = user_id
        session_ids.append("user_id")
    if agent_id:
        base_metadata["agent_id"] = agent_id
        effective_filters["agent_id"] = agent_id
        session_ids.append("agent_id")
    if run_id:
        base_metadata["run_id"] = run_id
        effective_filters["run_id"] = run_id
        session_ids.append("run_id")

    if not session_ids:
        raise FadeMemValidationError(
            "At least one of 'user_id', 'agent_id', or 'run_id' must be provided.",
            error_code="VALIDATION_001",
            details={"provided_ids": {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}},
            suggestion="Provide at least one identifier to scope the memory operation.",
        )

    resolved_actor_id = actor_id or effective_filters.get("actor_id")
    if resolved_actor_id:
        effective_filters["actor_id"] = resolved_actor_id

    return base_metadata, effective_filters


def process_telemetry_filters(filters: Optional[Dict[str, Any]]) -> Tuple[List[str], Dict[str, str]]:
    if not filters:
        return [], {}

    encoded_ids: Dict[str, str] = {}
    for key in ("user_id", "agent_id", "run_id"):
        if key in filters and filters[key] is not None:
            encoded_ids[key] = hashlib.md5(str(filters[key]).encode()).hexdigest()
    return list(filters.keys()), encoded_ids


def _value_matches_operator(value: Any, operator: str, expected: Any) -> bool:
    if operator == "eq":
        return value == expected
    if operator == "ne":
        return value != expected
    if operator == "gt":
        return value is not None and value > expected
    if operator == "gte":
        return value is not None and value >= expected
    if operator == "lt":
        return value is not None and value < expected
    if operator == "lte":
        return value is not None and value <= expected
    if operator == "in":
        return value in expected
    if operator == "nin":
        return value not in expected
    if operator == "contains":
        return isinstance(value, str) and str(expected) in value
    if operator == "icontains":
        return isinstance(value, str) and str(expected).lower() in value.lower()
    return False


def _match_condition(value: Any, condition: Any) -> bool:
    if condition == "*":
        return value is not None
    if not isinstance(condition, dict):
        return value == condition

    for operator, expected in condition.items():
        if not _value_matches_operator(value, operator, expected):
            return False
    return True


def matches_filters(data: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> bool:
    if not filters:
        return True

    for key, condition in filters.items():
        if key == "AND":
            if not isinstance(condition, list):
                return False
            if not all(matches_filters(data, sub) for sub in condition):
                return False
            continue
        if key == "OR":
            if not isinstance(condition, list):
                return False
            if not any(matches_filters(data, sub) for sub in condition):
                return False
            continue
        if key == "NOT":
            if not isinstance(condition, list):
                return False
            if any(matches_filters(data, sub) for sub in condition):
                return False
            continue

        value = data.get(key)
        if not _match_condition(value, condition):
            return False

    return True


def normalize_categories(categories: Optional[Iterable[str]]) -> List[str]:
    if not categories:
        return []
    return [str(c).strip() for c in categories if str(c).strip()]
