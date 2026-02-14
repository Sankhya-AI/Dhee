"""Policy gateway helpers for Engram v2."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set


ALL_CONFIDENTIALITY_SCOPES = ["work", "personal", "finance", "health", "private"]
CONFIDENTIALITY_SCOPES = set(ALL_CONFIDENTIALITY_SCOPES)
DEFAULT_CAPABILITIES = [
    "search",
    "propose_write",
    "read_scene",
    "review_commits",
    "resolve_conflicts",
    "read_digest",
    "read_trust",
    "manage_namespaces",
    "run_sleep_cycle",
]
HANDOFF_CAPABILITIES = ["read_handoff", "write_handoff"]
SENSITIVE_HINTS = {
    "finance": {"finance", "bank", "salary", "invoice", "tax", "payment", "credit"},
    "health": {"health", "medical", "doctor", "diagnosis", "therapy", "medication"},
    "private": {"password", "secret", "token", "api_key", "apikey", "private"},
    "personal": {"family", "relationship", "home", "personal"},
}


@dataclass
class PolicyDecision:
    allowed: bool
    masked: bool = False
    reason: Optional[str] = None


def feature_enabled(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_confidentiality_scope(scope: Optional[str]) -> str:
    if not scope:
        return "work"
    scope_value = str(scope).strip().lower()
    if scope_value in CONFIDENTIALITY_SCOPES:
        return scope_value
    return "work"


def detect_confidentiality_scope(
    *,
    categories: Optional[Iterable[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    content: Optional[str] = None,
    explicit_scope: Optional[str] = None,
) -> str:
    if explicit_scope:
        return normalize_confidentiality_scope(explicit_scope)

    metadata = metadata or {}
    meta_scope = metadata.get("confidentiality_scope") or metadata.get("scope_confidentiality")
    if meta_scope:
        return normalize_confidentiality_scope(meta_scope)

    terms = set()
    for category in categories or []:
        terms.update(str(category).lower().replace("/", " ").replace("_", " ").split())
    if content:
        terms.update(str(content).lower().split())

    for scope, hints in SENSITIVE_HINTS.items():
        if terms & hints:
            return scope
    return "work"


def token_required_for_agent(agent_id: Optional[str]) -> bool:
    if not feature_enabled("ENGRAM_V2_POLICY_GATEWAY", default=True):
        return False
    return bool(agent_id)


def default_allowed_scopes() -> List[str]:
    return list(ALL_CONFIDENTIALITY_SCOPES)


def is_trusted_local_request(client_host: Optional[str]) -> bool:
    if client_host is None:
        return False
    host = str(client_host).strip().lower()
    return host in {
        "127.0.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "localhost",
        "testclient",
    }


def _build_masked_shape(item: Dict[str, Any]) -> Dict[str, Any]:
    created_at = item.get("created_at") or item.get("timestamp")
    scope = normalize_confidentiality_scope(item.get("confidentiality_scope"))
    return {
        "id": item.get("id"),
        "type": f"{scope}_event" if scope != "work" else "memory_event",
        "time": created_at,
        "importance": item.get("importance", 0.5),
        "details": "[REDACTED]",
        "masked": True,
    }


def enforce_scope_on_item(
    item: Dict[str, Any],
    allowed_scopes: Optional[Set[str]],
) -> Dict[str, Any]:
    if not feature_enabled("ENGRAM_V2_POLICY_GATEWAY", default=True):
        visible = dict(item)
        visible["masked"] = False
        return visible

    if allowed_scopes is None:
        visible = dict(item)
        visible["masked"] = False
        return visible

    if not allowed_scopes:
        return _build_masked_shape(item)

    scope = normalize_confidentiality_scope(item.get("confidentiality_scope"))
    if scope in allowed_scopes:
        item = dict(item)
        item["masked"] = False
        return item

    return _build_masked_shape(item)


def enforce_scope_on_results(
    results: List[Dict[str, Any]],
    allowed_scopes: Optional[Iterable[str]],
) -> List[Dict[str, Any]]:
    scope_set = {normalize_confidentiality_scope(s) for s in (allowed_scopes or [])}
    return [enforce_scope_on_item(r, scope_set) for r in results]
