"""Canonical DheeFS URI helpers.

The virtual shell remains path-first because agents and developers already
understand ``/state`` and ``/handoff``.  ``dhee://`` is the stable address form
for cross-tool references, docs, and future SDK boundaries.
"""

from __future__ import annotations

from urllib.parse import urlparse


_ALIASES = {
    "/state/current": "/state/current.md",
    "/state/card": "/state/card.xml",
    "/state/decisions": "/state/decisions.md",
    "/state/superseded": "/state/superseded.md",
    "/state/history": "/state/history.md",
    "/handoff/latest": "/handoff/latest.md",
    "/handoff/snapshot": "/handoff/snapshot.json",
    "/sessions/latest": "/sessions/latest.md",
    "/shared/task-results": "/shared/task-results",
}


def normalize_dhee_uri(value: str) -> str:
    """Return a DheeFS path for either a path or a ``dhee://`` URI.

    Examples:
        ``dhee://state/current`` -> ``/state/current.md``
        ``dhee://router/ptr/R-abc`` -> ``/router/ptr/R-abc``
        ``/dhee/state/current.md`` -> ``/state/current.md`` later in
        ``ContextWorkspace.normalize_path``.
    """
    raw = str(value or "").strip()
    if not raw.startswith("dhee://") and not raw.startswith("dhee:/"):
        return raw

    parsed = urlparse(raw)
    if parsed.scheme != "dhee":
        return raw

    # urlparse("dhee://state/current") stores "state" as netloc and
    # "/current" as path. urlparse("dhee:/state/current") stores the whole
    # virtual path in parsed.path. Support both forms.
    if parsed.netloc:
        path = f"/{parsed.netloc}{parsed.path or ''}"
    else:
        path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/") if len(path) > 1 else path

    if path.startswith("/agents/") and path.endswith("/memory"):
        return f"{path}.md"
    return _ALIASES.get(path, path)


__all__ = ["normalize_dhee_uri"]
