"""Tests for engram-bus package — in-memory hot path + SQLite handoff persistence."""

import time
import threading

import pytest

from engram_bus import Bus, Workspace
from engram_bus.pubsub import PubSub


# ── TestActiveMemory ──


class TestActiveMemory:
    def test_put_get_roundtrip(self):
        """Basic types: string, dict, list, number."""
        bus = Bus()
        bus.put("s", "hello")
        assert bus.get("s") == "hello"
        bus.put("d", {"a": 1, "b": [2, 3]})
        assert bus.get("d") == {"a": 1, "b": [2, 3]}
        bus.put("l", [1, "two", None])
        assert bus.get("l") == [1, "two", None]
        bus.put("n", 42)
        assert bus.get("n") == 42
        bus.close()

    def test_put_overwrites(self):
        bus = Bus()
        bus.put("k", "v1")
        assert bus.get("k") == "v1"
        bus.put("k", "v2")
        assert bus.get("k") == "v2"
        bus.close()

    def test_get_missing_returns_none(self):
        bus = Bus()
        assert bus.get("nonexistent") is None
        bus.close()

    def test_delete_returns_true_false(self):
        bus = Bus()
        bus.put("k", "v")
        assert bus.delete("k") is True
        assert bus.delete("k") is False
        bus.close()

    def test_keys_and_all(self):
        bus = Bus()
        bus.put("a", 1)
        bus.put("b", 2)
        bus.put("c", 3)
        assert sorted(bus.keys()) == ["a", "b", "c"]
        assert bus.all() == {"a": 1, "b": 2, "c": 3}
        bus.close()

    def test_clear_namespace(self):
        bus = Bus()
        bus.put("x", 1)
        bus.put("y", 2)
        count = bus.clear()
        assert count == 2
        assert bus.keys() == []
        bus.close()

    def test_namespace_isolation(self):
        bus = Bus()
        bus.put("k", "ns1", namespace="ns1")
        bus.put("k", "ns2", namespace="ns2")
        assert bus.get("k", namespace="ns1") == "ns1"
        assert bus.get("k", namespace="ns2") == "ns2"
        assert bus.get("k") is None  # default namespace untouched
        bus.close()

    def test_agent_filter_on_keys(self):
        bus = Bus()
        bus.put("a1key", "val", agent="agent1")
        bus.put("a2key", "val", agent="agent2")
        bus.put("shared", "val")
        assert bus.keys(agent="agent1") == ["a1key"]
        assert bus.keys(agent="agent2") == ["a2key"]
        bus.close()

    def test_clear_only_affects_namespace(self):
        bus = Bus()
        bus.put("a", 1, namespace="keep")
        bus.put("b", 2, namespace="clear")
        bus.clear(namespace="clear")
        assert bus.get("a", namespace="keep") == 1
        assert bus.get("b", namespace="clear") is None
        bus.close()


# ── TestTTL ──


class TestTTL:
    def test_ttl_expires_key(self):
        bus = Bus()
        bus.put("temp", "value", ttl=1)
        assert bus.get("temp") == "value"
        time.sleep(1.1)
        assert bus.get("temp") is None
        bus.close()

    def test_ttl_none_means_no_expiry(self):
        bus = Bus()
        bus.put("permanent", "value")
        time.sleep(0.1)
        assert bus.get("permanent") == "value"
        bus.close()

    def test_ttl_expired_key_excluded_from_keys(self):
        bus = Bus()
        bus.put("live", "yes")
        bus.put("dying", "soon", ttl=1)
        assert sorted(bus.keys()) == ["dying", "live"]
        time.sleep(1.1)
        assert bus.keys() == ["live"]
        bus.close()

    def test_ttl_expired_key_excluded_from_all(self):
        bus = Bus()
        bus.put("live", "yes")
        bus.put("dying", "soon", ttl=1)
        time.sleep(1.1)
        assert bus.all() == {"live": "yes"}
        bus.close()

    def test_ttl_overwrite_resets_ttl(self):
        bus = Bus()
        bus.put("k", "v1", ttl=1)
        time.sleep(0.5)
        bus.put("k", "v2", ttl=3)
        time.sleep(0.8)
        assert bus.get("k") == "v2"
        bus.close()

    def test_ttl_short_expiry(self):
        """Very short TTL (< 1s) works."""
        bus = Bus()
        bus.put("flash", "blink", ttl=1)
        assert bus.get("flash") == "blink"
        time.sleep(1.1)
        assert bus.get("flash") is None
        bus.close()


# ── TestPubSub ──


class TestPubSub:
    def test_subscribe_and_publish(self):
        ps = PubSub()
        received = []
        ps.subscribe("topic", lambda t, d, a: received.append((t, d, a)))
        count = ps.publish("topic", "hello", agent_id="sender")
        assert count == 1
        assert received == [("topic", "hello", "sender")]

    def test_multiple_subscribers(self):
        ps = PubSub()
        results = []
        ps.subscribe("t", lambda t, d, a: results.append("cb1"))
        ps.subscribe("t", lambda t, d, a: results.append("cb2"))
        count = ps.publish("t", "data")
        assert count == 2
        assert sorted(results) == ["cb1", "cb2"]

    def test_unsubscribe(self):
        ps = PubSub()
        results = []
        cb = lambda t, d, a: results.append("called")
        ps.subscribe("t", cb)
        ps.unsubscribe("t", cb)
        count = ps.publish("t", "data")
        assert count == 0
        assert results == []

    def test_publish_no_subscribers_returns_zero(self):
        ps = PubSub()
        assert ps.publish("empty", "data") == 0

    def test_callback_error_doesnt_break_others(self):
        ps = PubSub()
        results = []

        def bad_cb(t, d, a):
            raise RuntimeError("boom")

        ps.subscribe("t", bad_cb)
        ps.subscribe("t", lambda t, d, a: results.append("ok"))
        count = ps.publish("t", "data")
        assert count == 1  # bad_cb failed, second succeeded
        assert results == ["ok"]


# ── TestSignals ──


class TestSignals:
    def test_publish_logs_signal(self):
        bus = Bus()
        bus.publish("build", {"status": "pass"}, agent="ci")
        signals = bus.signals()
        assert len(signals) == 1
        assert signals[0]["topic"] == "build"
        assert signals[0]["data"] == {"status": "pass"}
        assert signals[0]["agent_id"] == "ci"
        bus.close()

    def test_signals_filter_by_topic(self):
        bus = Bus()
        bus.publish("build", "pass")
        bus.publish("deploy", "ok")
        bus.publish("build", "fail")
        signals = bus.signals(topic="build")
        assert len(signals) == 2
        assert all(s["topic"] == "build" for s in signals)
        bus.close()

    def test_signals_filter_by_agent(self):
        bus = Bus()
        bus.publish("t", "d1", agent="a1")
        bus.publish("t", "d2", agent="a2")
        signals = bus.signals(agent="a1")
        assert len(signals) == 1
        assert signals[0]["agent_id"] == "a1"
        bus.close()

    def test_signals_limit(self):
        bus = Bus()
        for i in range(10):
            bus.publish("t", i)
        signals = bus.signals(limit=3)
        assert len(signals) == 3
        bus.close()

    def test_signals_since_filter(self):
        bus = Bus()
        bus.publish("t", "old")
        time.sleep(0.05)
        from engram_bus.bus import _now
        ts = _now()
        time.sleep(0.05)
        bus.publish("t", "new")
        signals = bus.signals(since=ts)
        assert len(signals) == 1
        assert signals[0]["data"] == "new"
        bus.close()


# ── TestAgentRegistry ──


class TestAgentRegistry:
    def test_register_and_list(self):
        bus = Bus()
        bus.register("planner", metadata={"role": "planner"})
        bus.register("coder")
        agents = bus.agents()
        assert len(agents) == 2
        ids = {a["agent_id"] for a in agents}
        assert ids == {"planner", "coder"}
        bus.close()

    def test_register_updates_metadata(self):
        bus = Bus()
        bus.register("a1", metadata={"v": 1})
        bus.register("a1", metadata={"v": 2})
        agents = bus.agents()
        assert len(agents) == 1
        assert agents[0]["metadata"] == {"v": 2}
        bus.close()

    def test_touch_on_put(self):
        """put() with agent auto-registers and updates last_seen."""
        bus = Bus()
        bus.put("k", "v", agent="a1")
        agents = bus.agents()
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "a1"
        last_before = agents[0]["last_seen"]
        time.sleep(0.05)
        bus.put("k2", "v2", agent="a1")
        agents = bus.agents()
        assert agents[0]["last_seen"] > last_before
        bus.close()

    def test_register_explicit_then_put(self):
        bus = Bus()
        bus.register("a1", metadata={"role": "planner"})
        bus.put("k", "v", agent="a1")
        agents = bus.agents()
        assert len(agents) == 1
        assert agents[0]["metadata"] == {"role": "planner"}
        bus.close()


# ── TestTransfer ──


class TestTransfer:
    def test_transfer_specific_keys(self):
        bus = Bus()
        bus.put("a", 1, agent="src")
        bus.put("b", 2, agent="src")
        bus.put("c", 3, agent="src")
        result = bus.transfer("src", "dst", keys=["a", "c"])
        assert result["transferred"] == 2
        assert sorted(result["keys"]) == ["a", "c"]
        assert sorted(bus.keys(agent="dst")) == ["a", "c"]
        bus.close()

    def test_transfer_all_keys(self):
        bus = Bus()
        bus.put("x", 10, agent="src")
        bus.put("y", 20, agent="src")
        result = bus.transfer("src", "dst")
        assert result["transferred"] == 2
        bus.close()

    def test_transfer_logs_receipt(self):
        bus = Bus()
        bus.put("k", "v", agent="from")
        bus.transfer("from", "to", keys=["k"])
        transfers = bus.transfers()
        assert len(transfers) == 1
        assert transfers[0]["from_agent"] == "from"
        assert transfers[0]["to_agent"] == "to"
        assert transfers[0]["keys"] == ["k"]
        bus.close()

    def test_query_transfers_by_agent(self):
        bus = Bus()
        bus.put("a", 1, agent="a1")
        bus.put("b", 2, agent="a2")
        bus.transfer("a1", "a3", keys=["a"])
        bus.transfer("a2", "a3", keys=["b"])
        transfers = bus.transfers(agent="a1")
        assert len(transfers) == 1
        assert transfers[0]["from_agent"] == "a1"
        bus.close()


# ── TestWorkspace ──


class TestWorkspace:
    def test_workspace_scopes_to_namespace(self):
        bus = Bus()
        ws = bus.workspace("project1")
        ws.put("status", "active")
        assert bus.get("status") is None
        assert ws.get("status") == "active"
        bus.close()

    def test_workspace_isolation(self):
        bus = Bus()
        ws1 = bus.workspace("ws1")
        ws2 = bus.workspace("ws2")
        ws1.put("key", "from_ws1")
        ws2.put("key", "from_ws2")
        assert ws1.get("key") == "from_ws1"
        assert ws2.get("key") == "from_ws2"
        bus.close()

    def test_workspace_publish_subscribe(self):
        bus = Bus()
        ws = bus.workspace("myws")
        received = []
        ws.subscribe("events", lambda t, d, a: received.append(d))
        ws.publish("events", {"type": "test"})
        assert len(received) == 1
        assert received[0] == {"type": "test"}
        bus.close()

    def test_workspace_clear(self):
        bus = Bus()
        ws = bus.workspace("clearme")
        ws.put("a", 1)
        ws.put("b", 2)
        count = ws.clear()
        assert count == 2
        assert ws.all() == {}
        bus.close()


# ── TestBus ──


class TestBus:
    def test_bus_put_publishes_internal_event(self):
        bus = Bus()
        events = []
        bus.subscribe("__bus__.put.default", lambda t, d, a: events.append(d))
        bus.put("k", "v", agent="tester")
        assert len(events) == 1
        assert events[0]["key"] == "k"
        assert events[0]["value"] == "v"
        assert events[0]["agent"] == "tester"
        bus.close()

    def test_bus_context_manager(self):
        with Bus() as bus:
            bus.put("ctx", "managed")
            assert bus.get("ctx") == "managed"

    def test_bus_snapshot_restore(self):
        bus = Bus()
        bus.put("a", 1)
        bus.put("b", {"nested": True})
        snap = bus.snapshot()
        assert snap == {"a": 1, "b": {"nested": True}}

        bus2 = Bus()
        count = bus2.restore(snap)
        assert count == 2
        assert bus2.get("a") == 1
        assert bus2.get("b") == {"nested": True}
        bus.close()
        bus2.close()

    def test_bus_thread_safety(self):
        """Concurrent puts from multiple threads shouldn't crash."""
        bus = Bus()
        errors = []

        def writer(n):
            try:
                for i in range(100):
                    bus.put(f"key-{n}-{i}", i, agent=f"agent-{n}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(bus.keys()) == 400
        bus.close()

    def test_bus_all_data_lost_on_close(self):
        """After close, a new Bus has no data — that's the point."""
        bus1 = Bus()
        bus1.put("ephemeral", "data")
        bus1.close()

        bus2 = Bus()
        assert bus2.get("ephemeral") is None
        bus2.close()


# ── TestServer ──


class TestServer:
    def test_server_client_put_get(self):
        from engram_bus.server import BusServer, BusClient

        bus = Bus()
        server = BusServer(bus, host="127.0.0.1", port=0)
        server.start()
        host, port = server.address()

        try:
            client = BusClient(host, port)
            client.put("hello", "world", agent="test-agent")
            val = client.get("hello")
            assert val == "world"
            assert bus.get("hello") == "world"
            client.close()
        finally:
            server.stop()
            bus.close()

    def test_server_client_publish(self):
        from engram_bus.server import BusServer, BusClient

        bus = Bus()
        server = BusServer(bus, host="127.0.0.1", port=0)
        server.start()
        host, port = server.address()

        try:
            client = BusClient(host, port)
            client.publish("build", {"status": "pass"}, agent="ci")
            time.sleep(0.1)
            signals = bus.signals(topic="build")
            assert len(signals) >= 1
            assert signals[0]["data"] == {"status": "pass"}
            client.close()
        finally:
            server.stop()
            bus.close()

    def test_server_client_subscribe(self):
        from engram_bus.server import BusServer, BusClient

        bus = Bus()
        server = BusServer(bus, host="127.0.0.1", port=0)
        server.start()
        host, port = server.address()

        try:
            received = []
            client = BusClient(host, port)
            client.subscribe("events", lambda t, d, a: received.append(d))
            client.publish("events", {"msg": "hello"}, agent="tester")
            assert len(received) == 1
            assert received[0] == {"msg": "hello"}
            signals = client.signals(topic="events")
            assert len(signals) >= 1
            client.close()
        finally:
            server.stop()
            bus.close()


# ── TestHandoffSessions ──


class TestHandoffSessions:
    def test_save_and_get_session(self):
        bus = Bus()
        sid = bus.save_session("agent-1", task_summary="refactor auth")
        session = bus.get_session(session_id=sid)
        assert session is not None
        assert session["agent_id"] == "agent-1"
        assert session["task_summary"] == "refactor auth"
        assert session["status"] == "active"
        assert session["decisions"] == []
        assert session["files_touched"] == []
        bus.close()

    def test_get_session_by_agent_id(self):
        bus = Bus()
        bus.save_session("agent-1", task_summary="first")
        bus.save_session("agent-1", task_summary="second")
        session = bus.get_session(agent_id="agent-1")
        assert session is not None
        assert session["task_summary"] == "second"  # most recent
        bus.close()

    def test_get_session_not_found(self):
        bus = Bus()
        assert bus.get_session(session_id="nonexistent") is None
        bus.close()

    def test_list_sessions(self):
        bus = Bus()
        bus.save_session("a1", task_summary="t1")
        bus.save_session("a2", task_summary="t2")
        bus.save_session("a1", task_summary="t3")
        all_sessions = bus.list_sessions()
        assert len(all_sessions) == 3
        a1_sessions = bus.list_sessions(agent_id="a1")
        assert len(a1_sessions) == 2
        bus.close()

    def test_list_sessions_by_status(self):
        bus = Bus()
        sid = bus.save_session("a1")
        bus.save_session("a2")
        bus.update_session(sid, status="completed")
        active = bus.list_sessions(status="active")
        completed = bus.list_sessions(status="completed")
        assert len(active) == 1
        assert len(completed) == 1
        bus.close()

    def test_update_session(self):
        bus = Bus()
        sid = bus.save_session("agent-1")
        bus.update_session(
            sid,
            status="paused",
            decisions=["use JWT"],
            files_touched=["auth.py"],
            todos=["add tests"],
            metadata={"priority": "high"},
        )
        session = bus.get_session(session_id=sid)
        assert session["status"] == "paused"
        assert session["decisions"] == ["use JWT"]
        assert session["files_touched"] == ["auth.py"]
        assert session["todos"] == ["add tests"]
        assert session["metadata"] == {"priority": "high"}
        bus.close()

    def test_session_with_repo(self):
        bus = Bus()
        sid = bus.save_session("agent-1", repo="my-project")
        session = bus.get_session(session_id=sid)
        assert session["repo"] == "my-project"
        bus.close()


# ── TestHandoffLanes ──


class TestHandoffLanes:
    def test_open_and_get_lane(self):
        bus = Bus()
        sid = bus.save_session("agent-1")
        lid = bus.open_lane(sid, "agent-1", "agent-2", context={"task": "review"})
        lane = bus.get_lane(lid)
        assert lane is not None
        assert lane["from_agent"] == "agent-1"
        assert lane["to_agent"] == "agent-2"
        assert lane["status"] == "open"
        assert lane["context"] == {"task": "review"}
        bus.close()

    def test_list_lanes_by_session(self):
        bus = Bus()
        sid1 = bus.save_session("a1")
        sid2 = bus.save_session("a2")
        bus.open_lane(sid1, "a1", "a2")
        bus.open_lane(sid1, "a1", "a3")
        bus.open_lane(sid2, "a2", "a3")
        lanes1 = bus.list_lanes(session_id=sid1)
        assert len(lanes1) == 2
        all_lanes = bus.list_lanes()
        assert len(all_lanes) == 3
        bus.close()

    def test_close_lane(self):
        bus = Bus()
        sid = bus.save_session("a1")
        lid = bus.open_lane(sid, "a1", "a2")
        bus.close_lane(lid)
        lane = bus.get_lane(lid)
        assert lane["status"] == "closed"
        bus.close()

    def test_get_lane_not_found(self):
        bus = Bus()
        assert bus.get_lane("nonexistent") is None
        bus.close()


# ── TestHandoffCheckpoints ──


class TestHandoffCheckpoints:
    def test_checkpoint_and_list(self):
        bus = Bus()
        sid = bus.save_session("agent-1")
        cid = bus.checkpoint(sid, "agent-1", {"state": "in_progress", "memory_ids": [1, 2, 3]})
        checkpoints = bus.list_checkpoints(session_id=sid)
        assert len(checkpoints) == 1
        assert checkpoints[0]["id"] == cid
        assert checkpoints[0]["snapshot"]["state"] == "in_progress"
        assert checkpoints[0]["snapshot"]["memory_ids"] == [1, 2, 3]
        bus.close()

    def test_checkpoint_with_lane(self):
        bus = Bus()
        sid = bus.save_session("a1")
        lid = bus.open_lane(sid, "a1", "a2")
        cid = bus.checkpoint(sid, "a1", {"step": 1}, lane_id=lid)
        checkpoints = bus.list_checkpoints(lane_id=lid)
        assert len(checkpoints) == 1
        assert checkpoints[0]["lane_id"] == lid
        bus.close()

    def test_multiple_checkpoints(self):
        bus = Bus()
        sid = bus.save_session("a1")
        bus.checkpoint(sid, "a1", {"step": 1})
        bus.checkpoint(sid, "a1", {"step": 2})
        bus.checkpoint(sid, "a1", {"step": 3})
        checkpoints = bus.list_checkpoints(session_id=sid)
        assert len(checkpoints) == 3
        bus.close()

    def test_lazy_store_init(self):
        """Handoff store initializes lazily on first handoff call."""
        bus = Bus()  # no db_path
        assert bus._store is None
        sid = bus.save_session("agent-1")
        assert bus._store is not None
        session = bus.get_session(session_id=sid)
        assert session["agent_id"] == "agent-1"
        bus.close()


# ── TestHandoffServer ──


class TestHandoffServer:
    def test_server_handoff_session_roundtrip(self):
        from engram_bus.server import BusServer, BusClient

        bus = Bus()
        server = BusServer(bus, host="127.0.0.1", port=0)
        server.start()
        host, port = server.address()

        try:
            client = BusClient(host, port)
            sid = client.save_session("agent-1", task_summary="test task")
            session = client.get_session(session_id=sid)
            assert session is not None
            assert session["task_summary"] == "test task"

            client.update_session(sid, status="completed")
            session = client.get_session(session_id=sid)
            assert session["status"] == "completed"

            sessions = client.list_sessions(agent_id="agent-1")
            assert len(sessions) == 1
            client.close()
        finally:
            server.stop()
            bus.close()

    def test_server_handoff_lanes(self):
        from engram_bus.server import BusServer, BusClient

        bus = Bus()
        server = BusServer(bus, host="127.0.0.1", port=0)
        server.start()
        host, port = server.address()

        try:
            client = BusClient(host, port)
            sid = client.save_session("a1")
            lid = client.open_lane(sid, "a1", "a2", context={"task": "review"})
            lane = client.get_lane(lid)
            assert lane["from_agent"] == "a1"
            assert lane["context"] == {"task": "review"}

            client.close_lane(lid)
            lane = client.get_lane(lid)
            assert lane["status"] == "closed"

            lanes = client.list_lanes(session_id=sid)
            assert len(lanes) == 1
            client.close()
        finally:
            server.stop()
            bus.close()

    def test_server_handoff_checkpoints(self):
        from engram_bus.server import BusServer, BusClient

        bus = Bus()
        server = BusServer(bus, host="127.0.0.1", port=0)
        server.start()
        host, port = server.address()

        try:
            client = BusClient(host, port)
            sid = client.save_session("a1")
            cid = client.checkpoint(sid, "a1", {"state": "done"})
            checkpoints = client.list_checkpoints(session_id=sid)
            assert len(checkpoints) == 1
            assert checkpoints[0]["snapshot"]["state"] == "done"
            client.close()
        finally:
            server.stop()
            bus.close()
