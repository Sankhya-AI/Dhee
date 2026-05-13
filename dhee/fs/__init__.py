"""DheeFS virtual learning/context shell."""

from dhee.fs.types import DheeFSEntry, DheeFSError, DheeFSResult, DheeMount
from dhee.fs.uri import normalize_dhee_uri
from dhee.fs.workspace import CommandRegistry, ContextWorkspace

__all__ = [
    "CommandRegistry",
    "ContextWorkspace",
    "DheeFSEntry",
    "DheeFSError",
    "DheeFSResult",
    "DheeMount",
    "normalize_dhee_uri",
]
