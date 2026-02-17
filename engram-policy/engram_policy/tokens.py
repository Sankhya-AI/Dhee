"""CapabilityToken — short-lived access grants for cross-agent operations."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TokenClaims:
    """Decoded token claims."""
    agent_id: str
    scopes: list[str]
    capabilities: list[str]
    issued_at: float
    expires_at: float
    token_id: str


class CapabilityToken:
    """Short-lived access grant for cross-agent operations.

    Tokens are stored in memory with TTL for automatic expiration.
    """

    def __init__(self, memory: Any, user_id: str = "system") -> None:
        self._memory = memory
        self._user_id = user_id

    def create(self, *, agent_id: str, scopes: list[str],
               capabilities: list[str], ttl_minutes: int = 60) -> str:
        """Create a capability token. Stored as a memory."""
        now = time.time()
        token_id = secrets.token_hex(16)

        # Create a simple signed token (hash-based, not JWT for zero-dep)
        payload = {
            "agent_id": agent_id,
            "scopes": scopes,
            "capabilities": capabilities,
            "issued_at": now,
            "expires_at": now + ttl_minutes * 60,
            "token_id": token_id,
        }
        payload_json = json.dumps(payload, sort_keys=True)
        signature = hashlib.sha256(payload_json.encode()).hexdigest()[:16]
        token = f"{token_id}.{signature}"

        # Store in memory for validation
        self._memory.add(
            f"Capability token for {agent_id}: scopes={scopes}",
            user_id=self._user_id,
            metadata={
                "memory_type": "capability_token",
                "token_id": token_id,
                "token_agent_id": agent_id,
                "token_scopes": scopes,
                "token_capabilities": capabilities,
                "token_issued_at": now,
                "token_expires_at": now + ttl_minutes * 60,
                "token_revoked": False,
            },
            categories=["tokens"],
            infer=False,
        )

        return token

    def validate(self, token: str) -> TokenClaims | None:
        """Validate and decode a token."""
        parts = token.split(".")
        if len(parts) != 2:
            return None

        token_id = parts[0]

        # Find token in memory
        results = self._memory.get_all(
            user_id=self._user_id,
            filters={"memory_type": "capability_token", "token_id": token_id},
            limit=1,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        if not items:
            return None

        md = items[0].get("metadata", {})

        # Check revocation
        if md.get("token_revoked", False):
            return None

        # Check expiration
        if time.time() > md.get("token_expires_at", 0):
            return None

        return TokenClaims(
            agent_id=md.get("token_agent_id", ""),
            scopes=md.get("token_scopes", []),
            capabilities=md.get("token_capabilities", []),
            issued_at=md.get("token_issued_at", 0),
            expires_at=md.get("token_expires_at", 0),
            token_id=token_id,
        )

    def revoke(self, token: str) -> bool:
        """Revoke a token early."""
        parts = token.split(".")
        if len(parts) != 2:
            return False

        token_id = parts[0]
        results = self._memory.get_all(
            user_id=self._user_id,
            filters={"memory_type": "capability_token", "token_id": token_id},
            limit=1,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        if not items:
            return False

        md = dict(items[0].get("metadata", {}))
        md["token_revoked"] = True
        self._memory.update(items[0]["id"], {"metadata": md})
        return True
