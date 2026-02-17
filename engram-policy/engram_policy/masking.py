"""DataMasker — mask sensitive fields for out-of-scope queries."""

from __future__ import annotations

from typing import Any

from engram_policy.scopes import match_scope

REDACTED = "[REDACTED]"


class DataMasker:
    """Mask sensitive fields for out-of-scope queries.

    Fields annotated with scope requirements are replaced with [REDACTED]
    if the requesting agent doesn't have the required scope.
    """

    def __init__(self, scope_map: dict[str, list[str]] | None = None) -> None:
        """
        Args:
            scope_map: Maps field names to required scopes.
                       e.g. {"password": ["sensitive"], "email": ["personal"]}
        """
        self._scope_map = scope_map or {}

    def mask(self, data: dict, agent_scopes: list[str]) -> dict:
        """Return data with out-of-scope fields replaced with [REDACTED]."""
        result = {}
        for key, value in data.items():
            required = self._scope_map.get(key)
            if required and not match_scope(required, agent_scopes):
                result[key] = REDACTED
            elif isinstance(value, dict):
                result[key] = self.mask(value, agent_scopes)
            else:
                result[key] = value
        return result
