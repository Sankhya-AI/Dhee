"""HTTP auth helpers for the Dhee agent runtime sidecar."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException


def require_bearer_token(authorization: Optional[str], token: Optional[str] = None) -> None:
    """Validate bearer auth when a sidecar token is configured."""

    expected = token if token is not None else os.getenv("DHEE_HTTP_TOKEN")
    if not expected:
        return
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    supplied = authorization[len(prefix) :].strip()
    if supplied != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
