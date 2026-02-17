"""Scope definitions and matching for policy evaluation."""

from __future__ import annotations

import fnmatch


# Well-known data scopes
SCOPES = {
    "work": "Work-related data (code, tasks, docs)",
    "personal": "Personal user data",
    "system": "System configuration and internals",
    "public": "Publicly accessible data",
    "sensitive": "Credentials, tokens, secrets",
}


def match_resource(pattern: str, resource: str) -> bool:
    """Check if a resource matches a pattern (supports glob-style wildcards).

    Examples:
        match_resource("production/*", "production/db") -> True
        match_resource("*.py", "main.py") -> True
        match_resource("code/src/**", "code/src/foo/bar.py") -> True
    """
    # Normalize: ** matches any depth
    if "**" in pattern:
        pattern = pattern.replace("**", "*")
    return fnmatch.fnmatch(resource, pattern)


def match_scope(required_scopes: list[str], agent_scopes: list[str]) -> bool:
    """Check if agent has any of the required scopes."""
    if not required_scopes:
        return True
    return bool(set(required_scopes) & set(agent_scopes))
