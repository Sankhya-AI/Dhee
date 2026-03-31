"""Scope/visibility logic for memory access control.

Extracted from memory/main.py — centralizes the scope resolution,
normalization, and access-control policy for multi-agent memory sharing.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional


SHAREABLE_CATEGORY_IDS = {
    "preferences",
    "procedures",
    "corrections",
}

SHAREABLE_CATEGORY_HINTS = (
    "preference",
    "workflow",
    "procedure",
    "coding",
    "code",
    "style",
    "tooling",
    "editor",
)

SCOPE_VALUES = {"agent", "connector", "category", "global"}
DEFAULT_SCOPE_WEIGHTS = {
    "agent": 1.0,
    "connector": 0.97,
    "category": 0.94,
    "global": 0.92,
}


class MemoryScope(str, Enum):
    AGENT = "agent"
    CONNECTOR = "connector"
    CATEGORY = "category"
    GLOBAL = "global"


class ScopeResolver:
    """Stateless scope resolution and access-control policy.

    Takes a scope_config (from MemoryConfig) at init.
    All methods are deterministic — no I/O, no LLM.
    """

    def __init__(self, scope_config=None):
        self.scope_config = scope_config

    def normalize_scope(self, scope: Optional[str]) -> Optional[str]:
        if scope is None:
            return None
        value = str(scope).strip().lower()
        return value if value in SCOPE_VALUES else None

    def normalize_agent_category(self, category: Optional[str]) -> Optional[str]:
        if category is None:
            return None
        value = str(category).strip().lower()
        return value or None

    def normalize_connector_id(self, connector_id: Optional[str]) -> Optional[str]:
        if connector_id is None:
            return None
        value = str(connector_id).strip().lower()
        return value or None

    def infer_scope(
        self,
        *,
        scope: Optional[str],
        connector_id: Optional[str],
        agent_category: Optional[str],
        policy_explicit: bool,
        agent_id: Optional[str],
    ) -> str:
        normalized_scope = self.normalize_scope(scope)
        normalized_connector_id = self.normalize_connector_id(connector_id)
        normalized_agent_category = self.normalize_agent_category(agent_category)

        if normalized_scope:
            if normalized_scope == MemoryScope.CONNECTOR.value and not normalized_connector_id:
                return MemoryScope.CATEGORY.value if normalized_agent_category else MemoryScope.GLOBAL.value
            if normalized_scope == MemoryScope.CATEGORY.value and not normalized_agent_category:
                return MemoryScope.GLOBAL.value
            if normalized_scope == MemoryScope.AGENT.value and not agent_id:
                return MemoryScope.GLOBAL.value
            return normalized_scope

        if normalized_connector_id:
            return MemoryScope.CONNECTOR.value
        if policy_explicit:
            return MemoryScope.CATEGORY.value if normalized_agent_category else MemoryScope.GLOBAL.value
        if agent_id:
            return MemoryScope.AGENT.value
        return MemoryScope.GLOBAL.value

    def resolve_scope(self, memory: Dict[str, Any]) -> str:
        metadata = memory.get("metadata", {}) or {}
        scope = self.normalize_scope(metadata.get("scope"))
        if scope:
            return scope

        return self.infer_scope(
            scope=None,
            connector_id=metadata.get("connector_id"),
            agent_category=metadata.get("agent_category"),
            policy_explicit=bool(metadata.get("policy_explicit")),
            agent_id=memory.get("agent_id"),
        )

    def get_scope_weight(self, scope: str) -> float:
        if self.scope_config:
            weight_map = {
                MemoryScope.AGENT.value: getattr(self.scope_config, "agent_weight", DEFAULT_SCOPE_WEIGHTS["agent"]),
                MemoryScope.CONNECTOR.value: getattr(self.scope_config, "connector_weight", DEFAULT_SCOPE_WEIGHTS["connector"]),
                MemoryScope.CATEGORY.value: getattr(self.scope_config, "category_weight", DEFAULT_SCOPE_WEIGHTS["category"]),
                MemoryScope.GLOBAL.value: getattr(self.scope_config, "global_weight", DEFAULT_SCOPE_WEIGHTS["global"]),
            }
        else:
            weight_map = DEFAULT_SCOPE_WEIGHTS
        return float(weight_map.get(scope, 1.0))

    def allows_scope(
        self,
        memory: Dict[str, Any],
        *,
        user_id: Optional[str],
        agent_id: Optional[str],
        agent_category: Optional[str],
        connector_ids: Optional[List[str]],
    ) -> bool:
        metadata = memory.get("metadata", {}) or {}
        stored_scope = self.normalize_scope(metadata.get("scope"))
        memory_agent_id = memory.get("agent_id")

        if stored_scope is None and not agent_category:
            if agent_id and memory_agent_id not in (None, agent_id):
                return is_shareable_memory(memory)
            return True

        scope = stored_scope or self.resolve_scope(memory)

        if scope == MemoryScope.GLOBAL.value:
            return True
        if scope == MemoryScope.AGENT.value:
            return bool(agent_id) and memory_agent_id == agent_id
        if scope == MemoryScope.CATEGORY.value:
            if not agent_category:
                return False
            mem_category = self.normalize_agent_category(metadata.get("agent_category"))
            return mem_category == self.normalize_agent_category(agent_category)
        if scope == MemoryScope.CONNECTOR.value:
            if not connector_ids:
                return False
            mem_connector = self.normalize_connector_id(metadata.get("connector_id"))
            if not mem_connector:
                return False
            normalized_ids = {
                cid
                for cid in (self.normalize_connector_id(c) for c in connector_ids)
                if cid
            }
            if mem_connector not in normalized_ids:
                return False
            request_category = self.normalize_agent_category(agent_category)
            mem_category = self.normalize_agent_category(metadata.get("agent_category"))
            if request_category and mem_category and request_category != mem_category:
                return False
            return True

        return True


def is_shareable_memory(memory: Dict[str, Any]) -> bool:
    """Check if a memory is shareable across agents."""
    categories = memory.get("categories") or []
    if isinstance(categories, str):
        import json
        try:
            categories = json.loads(categories)
        except (json.JSONDecodeError, TypeError):
            categories = [categories] if categories else []

    metadata = memory.get("metadata") or {}
    if isinstance(metadata, str):
        import json
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    for cat_id in categories:
        if str(cat_id).strip().lower() in SHAREABLE_CATEGORY_IDS:
            return True

    memory_text = str(memory.get("memory") or "").lower()
    if any(hint in memory_text for hint in SHAREABLE_CATEGORY_HINTS):
        return True

    scope = str(metadata.get("scope") or "").lower()
    if scope in ("global", "category"):
        return True

    return False
