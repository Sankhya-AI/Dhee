from typing import Any, Dict, Optional


class FadeMemError(Exception):
    """Base exception for engram errors."""

    def __init__(self, message: str, error_code: str = "FADEM_000", details: Optional[Dict[str, Any]] = None, suggestion: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.suggestion = suggestion


class FadeMemValidationError(FadeMemError):
    """Raised when input validation fails."""

    def __init__(self, message: str, error_code: str = "VALIDATION_000", details: Optional[Dict[str, Any]] = None, suggestion: Optional[str] = None):
        super().__init__(message, error_code=error_code, details=details, suggestion=suggestion)
