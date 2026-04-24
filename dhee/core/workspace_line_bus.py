"""In-process pub/sub fanout for the workspace information line.

Replaces the 1-second DB-poll loop that the SSE endpoint used to run.
Each SSE connection subscribes to a workspace (optionally filtered by
project_id and/or channel). Writers — ``emit_agent_activity`` and the
human-publish endpoint — push the message through ``publish()`` after
the DB write, and every matching subscriber gets it delivered in the
same event-loop tick.

Design notes:

* **In-process only.** The bus is a module-level singleton. Fine for
  single-worker uvicorn (our current hosting story). Multi-worker or
  multi-host would need Redis pub/sub or Postgres LISTEN/NOTIFY — we
  add that when hosted Dhee needs to fan out across workers.
* **Bounded queues with drop-oldest.** Slow subscribers must not wedge
  fast ones. Each subscriber queue has a cap; when full we drop the
  oldest message rather than block the publisher. The REST endpoint
  always has the authoritative DB copy, so a dropped message means the
  client plays catch-up on its next refresh — a sane degradation.
* **Fail-open on publish.** Publishing can never raise back into the
  caller. A bus failure must not roll back the DB write.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional, Set
from weakref import WeakSet


@dataclass(eq=False)
class _Subscriber:
    queue: asyncio.Queue
    workspace_id: str
    project_id: Optional[str] = None
    channel: Optional[str] = None

    def matches(self, message: Dict[str, Any]) -> bool:
        if str(message.get("workspace_id") or "") != self.workspace_id:
            return False
        if self.project_id:
            if str(message.get("project_id") or "") != self.project_id and str(
                message.get("target_project_id") or ""
            ) != self.project_id:
                return False
        if self.channel:
            if str(message.get("channel") or "") != self.channel:
                return False
        return True


@dataclass
class WorkspaceLineBus:
    max_queue: int = 128
    _subscribers: Set[_Subscriber] = field(default_factory=WeakSet)

    def publish(self, message: Dict[str, Any]) -> None:
        if not isinstance(message, dict):
            return
        workspace_id = str(message.get("workspace_id") or "")
        if not workspace_id:
            return
        for subscriber in list(self._subscribers):
            try:
                if not subscriber.matches(message):
                    continue
                queue = subscriber.queue
                if queue.full():
                    # Drop-oldest so a stuck subscriber doesn't block us.
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(message)
            except Exception:
                # Never let a bad subscriber bring down the publisher.
                continue

    def subscribe(
        self,
        *,
        workspace_id: str,
        project_id: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> _Subscriber:
        subscriber = _Subscriber(
            queue=asyncio.Queue(maxsize=self.max_queue),
            workspace_id=workspace_id,
            project_id=project_id or None,
            channel=channel or None,
        )
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: _Subscriber) -> None:
        self._subscribers.discard(subscriber)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


_BUS: Optional[WorkspaceLineBus] = None


def get_bus() -> WorkspaceLineBus:
    global _BUS
    if _BUS is None:
        _BUS = WorkspaceLineBus()
    return _BUS


def publish(message: Optional[Dict[str, Any]]) -> None:
    """Convenience — fail-open publish. Safe to call from write paths
    that must not propagate errors."""
    if not message:
        return
    try:
        get_bus().publish(message)
    except Exception:
        return


async def iter_messages(
    *,
    workspace_id: str,
    project_id: Optional[str] = None,
    channel: Optional[str] = None,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[Optional[Dict[str, Any]]]:
    """Async iterator yielding matching line messages as they arrive.

    Yields ``None`` every ``heartbeat_seconds`` so callers can emit
    SSE keep-alive frames. Honours cooperative cancellation via
    ``asyncio.CancelledError``.
    """
    bus = get_bus()
    subscriber = bus.subscribe(
        workspace_id=workspace_id,
        project_id=project_id,
        channel=channel,
    )
    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    subscriber.queue.get(), timeout=heartbeat_seconds
                )
                yield message
            except asyncio.TimeoutError:
                yield None
    finally:
        bus.unsubscribe(subscriber)
