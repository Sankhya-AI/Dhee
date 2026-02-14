"""Auth/session helpers for Engram v2 API."""

from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request

from engram_enterprise.policy import is_trusted_local_request


CLI_HINTS = {"engram-cli", "python-requests"}


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def get_token_from_request(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization")
    return extract_bearer_token(auth_header)


def is_trusted_local_client(request: Request) -> bool:
    client_host = request.client.host if request.client else None
    return is_trusted_local_request(client_host)


def is_trusted_direct_client(request: Request) -> bool:
    if not is_trusted_local_client(request):
        return False

    # Explicit override for CLI calls.
    client_hint = (request.headers.get("X-Engram-Client") or "").strip().lower()
    if client_hint == "cli":
        return True

    user_agent = (request.headers.get("User-Agent") or "").lower()
    return any(hint in user_agent for hint in CLI_HINTS)


def require_token_for_untrusted_request(request: Request, token: Optional[str]) -> None:
    if token:
        return
    if is_trusted_local_client(request):
        return
    raise HTTPException(status_code=401, detail="Bearer capability token required")


def enforce_session_issuer(request: Request) -> None:
    """Lock session minting to trusted local callers with optional admin secret."""
    if not is_trusted_local_client(request):
        raise HTTPException(status_code=403, detail="Session creation allowed only from local trusted clients")

    expected = (os.environ.get("ENGRAM_ADMIN_KEY") or "").strip()
    if not expected:
        return

    provided = (request.headers.get("X-Engram-Admin-Key") or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Invalid admin key for session creation")


def require_session_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=401, detail=str(exc))
