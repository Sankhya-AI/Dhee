"""Canonical event vocabulary + HarnessAdapter base class.

The canonical events below are the only names Dhee's core talks in.
Vendor adapters (Claude Code, Codex, …) map their own event strings
onto this enum; the core never sees the vendor name.

Adding a new harness is a three-step change: (1) define the adapter
subclass with an ``event_map``, (2) implement handlers for the canonical
events you care about, (3) register it in ``_ADAPTER_REGISTRY`` below.

Deliberately small. The goal is *a stable contract*, not an ORM.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class CanonicalEvent(str, Enum):
    """Harness-agnostic event vocabulary.

    The values are snake_case strings so the enum can be serialized and
    matched against JSON/log fields without losing fidelity.
    """

    SESSION_START = "session_start"
    USER_PROMPT = "user_prompt"
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    PRE_COMPACT = "pre_compact"
    SESSION_END = "session_end"


EventHandler = Callable[[Dict[str, Any]], Dict[str, Any]]


class HarnessAdapter:
    """Base adapter for a CLI/IDE harness.

    Subclasses provide two things:

      * ``event_map`` — a dict mapping the harness's native event names
        onto canonical ones. Unknown events are ignored (return ``{}``).
      * Handler implementations, either by overriding ``handle`` or by
        registering per-event callables via ``register``.

    The base class is intentionally transport-agnostic; it does NOT read
    stdin or write stdout. That plumbing belongs to the harness-specific
    entry point (e.g. ``python -m dhee.hooks.claude_code``).
    """

    name: str = "base"
    event_map: Dict[str, CanonicalEvent] = {}

    def __init__(self) -> None:
        self._handlers: Dict[CanonicalEvent, EventHandler] = {}

    # ------------------------------------------------------------------
    # Registration + translation
    # ------------------------------------------------------------------

    def register(self, event: CanonicalEvent, fn: EventHandler) -> None:
        self._handlers[event] = fn

    def translate(self, vendor_event: str) -> Optional[CanonicalEvent]:
        return self.event_map.get(vendor_event)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(self, vendor_event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Translate a vendor event name and invoke the handler.

        Returns ``{}`` (never raises) if the event is unknown or the
        handler fails. This matches the host-agent contract Dhee honors:
        hook errors never break the user's CLI.
        """
        canonical = self.translate(vendor_event)
        if canonical is None:
            return {}
        return self.handle(canonical, payload)

    def handle(
        self, event: CanonicalEvent, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        fn = self._handlers.get(event)
        if fn is None:
            return {}
        try:
            out = fn(payload)
            return out if isinstance(out, dict) else {}
        except Exception as exc:
            logger.debug("%s handler for %s failed: %s", self.name, event, exc)
            return {}


# ---------------------------------------------------------------------------
# Registry — lazy to keep import cost low.
# ---------------------------------------------------------------------------

_ADAPTER_REGISTRY: Dict[str, Callable[[], HarnessAdapter]] = {}


def register_adapter(name: str, factory: Callable[[], HarnessAdapter]) -> None:
    _ADAPTER_REGISTRY[name] = factory


def get_adapter(name: str) -> HarnessAdapter:
    factory = _ADAPTER_REGISTRY.get(name)
    if factory is None:
        raise KeyError(f"no harness adapter registered for {name!r}")
    return factory()


def known_adapters() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())


# Eager registration of the two first-class adapters. Imported lazily
# inside the factory so ``from dhee.harness import …`` stays cheap.


def _claude_code_factory() -> HarnessAdapter:
    from dhee.harness.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter()


def _codex_factory() -> HarnessAdapter:
    from dhee.harness.codex import CodexAdapter

    return CodexAdapter()


register_adapter("claude_code", _claude_code_factory)
register_adapter("codex", _codex_factory)
