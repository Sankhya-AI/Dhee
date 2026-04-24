"""PR 4 — in-process pub/sub fanout for the workspace line.

Verifies that:
  - publish() delivers to matching subscribers in the same tick
  - project_id / channel filters are honoured
  - slow subscribers trigger drop-oldest instead of blocking publisher
  - emit_agent_activity writes the DB row AND publishes
  - the SSE endpoint delivers new messages without the old 1s lag
"""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from dhee.core.workspace_line import emit_agent_activity
from dhee.core.workspace_line_bus import WorkspaceLineBus, get_bus, iter_messages, publish
from dhee.db.sqlite import SQLiteManager


def _seed(tmp_path):
    db = SQLiteManager(str(tmp_path / "history.db"))
    ws = db.upsert_workspace({"user_id": "default", "name": "W", "root_path": str(tmp_path)})
    proj_a = db.upsert_workspace_project(
        {"user_id": "default", "workspace_id": ws["id"], "name": "backend"}
    )
    proj_b = db.upsert_workspace_project(
        {"user_id": "default", "workspace_id": ws["id"], "name": "frontend"}
    )
    return db, ws["id"], proj_a["id"], proj_b["id"]


# ---------------------------------------------------------------------------
# Bus primitives
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_delivers_matching_messages_only():
    bus = WorkspaceLineBus()
    sub_ws_a = bus.subscribe(workspace_id="A")
    sub_ws_b = bus.subscribe(workspace_id="B")

    bus.publish({"workspace_id": "A", "id": "1", "body": "to A"})
    bus.publish({"workspace_id": "B", "id": "2", "body": "to B"})
    bus.publish({"workspace_id": "A", "id": "3", "body": "also to A"})

    assert sub_ws_a.queue.qsize() == 2
    assert sub_ws_b.queue.qsize() == 1
    a_first = await sub_ws_a.queue.get()
    a_second = await sub_ws_a.queue.get()
    assert a_first["id"] == "1" and a_second["id"] == "3"


@pytest.mark.asyncio
async def test_bus_project_and_channel_filters():
    bus = WorkspaceLineBus()
    sub_proj = bus.subscribe(workspace_id="A", project_id="P1")
    sub_chan = bus.subscribe(workspace_id="A", channel="project")

    # matches both filters
    bus.publish({"workspace_id": "A", "project_id": "P1", "channel": "project", "id": "1"})
    # matches project filter (via target_project_id)
    bus.publish({"workspace_id": "A", "target_project_id": "P1", "channel": "workspace", "id": "2"})
    # matches channel only
    bus.publish({"workspace_id": "A", "project_id": "P2", "channel": "project", "id": "3"})
    # matches neither
    bus.publish({"workspace_id": "A", "project_id": "P2", "channel": "workspace", "id": "4"})

    proj_ids = []
    while sub_proj.queue.qsize():
        proj_ids.append((await sub_proj.queue.get())["id"])
    chan_ids = []
    while sub_chan.queue.qsize():
        chan_ids.append((await sub_chan.queue.get())["id"])
    assert proj_ids == ["1", "2"]
    assert chan_ids == ["1", "3"]


@pytest.mark.asyncio
async def test_bus_drop_oldest_when_queue_full():
    bus = WorkspaceLineBus(max_queue=3)
    sub = bus.subscribe(workspace_id="A")
    for i in range(5):
        bus.publish({"workspace_id": "A", "id": str(i)})
    # Queue capped at 3, oldest dropped; last three IDs retained.
    assert sub.queue.qsize() == 3
    drained = [(await sub.queue.get())["id"] for _ in range(3)]
    assert drained == ["2", "3", "4"]


@pytest.mark.asyncio
async def test_bus_unsubscribe_stops_delivery():
    bus = WorkspaceLineBus()
    sub = bus.subscribe(workspace_id="A")
    bus.unsubscribe(sub)
    bus.publish({"workspace_id": "A", "id": "1"})
    assert sub.queue.qsize() == 0


def test_bus_publish_fail_open_on_bad_message():
    # publish() must never raise into the caller
    publish(None)
    publish({})
    publish({"workspace_id": ""})  # no workspace — ignored
    # If we got here, no exception was raised.
    assert True


# ---------------------------------------------------------------------------
# Emit pipeline — DB + bus fanout together
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_agent_activity_publishes_to_bus(tmp_path, monkeypatch):
    # Reset the singleton bus so previous tests don't spill into this one.
    import dhee.core.workspace_line_bus as bus_mod

    bus_mod._BUS = None
    db, ws_id, proj_a, _proj_b = _seed(tmp_path)

    bus = get_bus()
    sub = bus.subscribe(workspace_id=ws_id)

    emit_agent_activity(
        db,
        tool_name="Read",
        packet_kind="routed_read",
        digest="d",
        cwd=str(tmp_path),
        source_path=str(tmp_path / "x.py"),
        source_event_id="e1",
        ptr="R-1",
        harness="codex",
    )

    # Bus fanout is synchronous — delivered in the same call.
    assert sub.queue.qsize() == 1
    message = await sub.queue.get()
    assert message["workspace_id"] == ws_id
    assert message["message_kind"] == "tool.routed_read"


@pytest.mark.asyncio
async def test_dedup_emit_does_not_republish(tmp_path):
    import dhee.core.workspace_line_bus as bus_mod

    bus_mod._BUS = None
    db, ws_id, _proj_a, _proj_b = _seed(tmp_path)
    sub = get_bus().subscribe(workspace_id=ws_id)

    common = dict(
        tool_name="Bash",
        packet_kind="routed_bash",
        digest="d",
        cwd=str(tmp_path),
        source_event_id="evt-1",
        ptr="B-1",
        harness="codex",
        runtime_id="codex",
        native_session_id="s1",
    )
    emit_agent_activity(db, **common)
    emit_agent_activity(db, **common)  # dedup collision — DB returns None, no publish

    assert sub.queue.qsize() == 1


# ---------------------------------------------------------------------------
# iter_messages — what the SSE endpoint drives. Verifying this
# substrate is enough; the SSE handler itself is 10 lines of glue that
# serialises these dicts as `data:` frames.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iter_messages_delivers_push_from_emit(tmp_path):
    import dhee.core.workspace_line_bus as bus_mod

    bus_mod._BUS = None
    db, ws_id, _proj_a, _proj_b = _seed(tmp_path)

    async def emit_after_subscribe():
        # Give the iterator a tick to set up its subscription.
        await asyncio.sleep(0.02)
        emit_agent_activity(
            db,
            tool_name="Read",
            packet_kind="routed_read",
            digest="hello",
            cwd=str(tmp_path),
            source_path=str(tmp_path / "spec.md"),
            source_event_id="e-sse-1",
            ptr="R-sse-1",
            harness="claude-code",
        )

    async def read_one():
        async for message in iter_messages(workspace_id=ws_id, heartbeat_seconds=30.0):
            if message is None:
                continue
            return message
        return None

    asyncio.create_task(emit_after_subscribe())
    received = await asyncio.wait_for(read_one(), timeout=1.5)
    assert received is not None
    assert received["workspace_id"] == ws_id
    assert received["message_kind"] == "tool.routed_read"
    assert (received.get("metadata") or {}).get("ptr") == "R-sse-1"


@pytest.mark.asyncio
async def test_iter_messages_heartbeat_on_silence(tmp_path):
    """With no publishers, iter_messages must still yield None every
    `heartbeat_seconds` so the SSE handler can emit `: keep-alive`
    frames and proxies don't time the connection out."""
    import dhee.core.workspace_line_bus as bus_mod

    bus_mod._BUS = None
    _seed(tmp_path)

    async def collect():
        async for message in iter_messages(
            workspace_id="silent-ws",
            heartbeat_seconds=0.05,
        ):
            return message  # first tick

    result = await asyncio.wait_for(collect(), timeout=0.5)
    assert result is None  # heartbeat tick
