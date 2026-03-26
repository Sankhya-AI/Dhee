"""SHA-256 hashing utilities for memory dedup, trajectory identity, and skill signatures.

Three hash functions:
- content_hash(text)                    — memory dedup
- trajectory_hash(steps)                — episode identity
- skill_signature_hash(preconditions, steps, tags) — skill dedup
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence


def stable_json(obj: Any) -> str:
    """Deterministic JSON serialization (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(text: str) -> str:
    """SHA-256 hash of normalized content for memory deduplication."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def trajectory_hash(steps: Sequence[Dict[str, Any]]) -> str:
    """SHA-256 hash of trajectory steps for episode identity.

    Normalizes each step to (action, tool, args_hash) tuples so that
    result variations don't change the trajectory identity.
    """
    normalized = []
    for step in steps:
        action = str(step.get("action", "")).strip().lower()
        tool = str(step.get("tool", "")).strip().lower()
        # Hash the args separately so ordering doesn't matter
        args = step.get("args", {})
        if isinstance(args, dict):
            args_hash = hashlib.sha256(stable_json(args).encode("utf-8")).hexdigest()[:16]
        else:
            args_hash = hashlib.sha256(str(args).encode("utf-8")).hexdigest()[:16]
        normalized.append(f"{action}|{tool}|{args_hash}")

    combined = "\n".join(normalized)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def skill_signature_hash(
    preconditions: Sequence[str],
    steps: Sequence[str],
    tags: Sequence[str],
) -> str:
    """SHA-256 hash for skill deduplication (name excluded intentionally).

    Two skills with the same preconditions, steps, and tags are considered
    duplicates even if they have different names.
    """
    obj = {
        "preconditions": sorted(str(p).strip().lower() for p in preconditions),
        "steps": [str(s).strip().lower() for s in steps],
        "tags": sorted(str(t).strip().lower() for t in tags),
    }
    return hashlib.sha256(stable_json(obj).encode("utf-8")).hexdigest()


def structural_signature_hash(
    step_templates: Sequence[str],
    step_roles: Sequence[str],
    slot_names: Sequence[str],
) -> str:
    """SHA-256 hash of normalized templates + roles + sorted slot names.

    Two skills with the same structural signature share the same recipe
    structure, even if their slot values differ.
    """
    obj = {
        "templates": [str(t).strip().lower() for t in step_templates],
        "roles": [str(r).strip().lower() for r in step_roles],
        "slot_names": sorted(str(n).strip().lower() for n in slot_names),
    }
    return hashlib.sha256(stable_json(obj).encode("utf-8")).hexdigest()
