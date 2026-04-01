"""Compatibility shim for the historical ``engram`` package namespace.

The project was renamed to ``dhee`` but several legacy modules, tests, and
sidecar packages still import ``engram.*``. Keep that import surface working
by pointing Python's package search path at the live ``dhee`` package.
"""

from __future__ import annotations

import dhee as _dhee

from dhee import *  # noqa: F401,F403

__all__ = getattr(_dhee, "__all__", [])
__path__ = list(getattr(_dhee, "__path__", []))
__doc__ = _dhee.__doc__
__file__ = __file__
__package__ = "engram"
__version__ = getattr(_dhee, "__version__", "0.0.0")
