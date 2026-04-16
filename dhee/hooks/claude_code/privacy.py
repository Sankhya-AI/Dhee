"""Strip secrets from tool outputs before storing in Dhee memory.

Applied automatically by PostToolUse hook before ``dhee.remember()``.
Conservative: redacts aggressively. Better to lose a token than leak a key.
"""

from __future__ import annotations

import re

_SECRET_PATTERNS = [
    re.compile(r'(?:api[_-]?key|apikey|api_secret)\s*[:=]\s*["\']?[\w\-\.]{20,}["\']?', re.IGNORECASE),
    re.compile(r'Bearer\s+[\w\-\.]{20,}', re.IGNORECASE),
    re.compile(r'AKIA[A-Z0-9]{16}'),
    re.compile(r'(?:aws_secret_access_key|aws_access_key_id)\s*[:=]\s*["\']?[\w/\+]{20,}["\']?', re.IGNORECASE),
    re.compile(r'(?:password|passwd|secret|token|credential)\s*[:=]\s*["\']?[^\s"\']{8,}["\']?', re.IGNORECASE),
    re.compile(r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA )?PRIVATE KEY-----'),
    re.compile(r'gh[pousr]_[A-Za-z0-9_]{36,}'),
    re.compile(r'sk-ant-[a-zA-Z0-9\-]{20,}'),
    re.compile(r'sk-[a-zA-Z0-9\-]{20,}'),
    re.compile(r'(?:key|secret|token|auth)\s*[:=]\s*["\']?[a-f0-9]{32,}["\']?', re.IGNORECASE),
]

_REDACTED = "[REDACTED]"


def filter_secrets(text: str) -> str:
    """Remove likely secrets from text before memory storage."""
    if not text:
        return text
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_REDACTED, result)
    return result
