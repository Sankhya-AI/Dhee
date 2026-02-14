"""Cross-process TCP server and client for engram-bus.

Wire protocol: JSON lines — one JSON object per line (newline-delimited).

Request format:
    {"op": "<method>", ...params}

Response format:
    {"ok": true, ...result}   or   {"ok": false, "error": "message"}

Subscription push events:
    {"event": "signal", "topic": "...", "data": ..., "agent": "..."}
"""

import argparse
import json
import logging
import socket
import socketserver
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class _ClientHandler(socketserver.StreamRequestHandler):
    """Handles a single client connection."""

    def setup(self) -> None:
        super().setup()
        self._subscriptions: Dict[str, Callable] = {}
        self._write_lock = threading.Lock()

    def handle(self) -> None:
        bus = self.server.bus  # type: ignore[attr-defined]
        for raw_line in self.rfile:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                self._send({"ok": False, "error": "invalid JSON"})
                continue
            try:
                resp = self._dispatch(bus, req)
                self._send(resp)
            except Exception as e:
                self._send({"ok": False, "error": str(e)})

        # Cleanup subscriptions on disconnect
        for topic, cb in self._subscriptions.items():
            bus._pubsub.unsubscribe(topic, cb)

    def _dispatch(self, bus: Any, req: Dict) -> Dict:
        op = req.get("op", "")

        if op == "put":
            bus.put(
                req["key"],
                req.get("value"),
                agent=req.get("agent"),
                namespace=req.get("namespace", "default"),
                ttl=req.get("ttl"),
            )
            return {"ok": True}

        elif op == "get":
            val = bus.get(req["key"], namespace=req.get("namespace", "default"))
            return {"ok": True, "value": val}

        elif op == "delete":
            result = bus.delete(req["key"], namespace=req.get("namespace", "default"))
            return {"ok": True, "deleted": result}

        elif op == "keys":
            result = bus.keys(
                namespace=req.get("namespace", "default"),
                agent=req.get("agent"),
            )
            return {"ok": True, "keys": result}

        elif op == "all":
            result = bus.all(namespace=req.get("namespace", "default"))
            return {"ok": True, "data": result}

        elif op == "clear":
            count = bus.clear(namespace=req.get("namespace", "default"))
            return {"ok": True, "count": count}

        elif op == "publish":
            count = bus.publish(
                req["topic"],
                req.get("data"),
                agent=req.get("agent"),
            )
            return {"ok": True, "count": count}

        elif op == "subscribe":
            topic = req["topic"]
            if topic not in self._subscriptions:
                def make_cb(t: str) -> Callable:
                    def cb(_topic: str, data: Any, agent_id: Optional[str]) -> None:
                        self._send({"event": "signal", "topic": t, "data": data, "agent": agent_id})
                    return cb
                cb = make_cb(topic)
                self._subscriptions[topic] = cb
                bus.subscribe(topic, cb)
            return {"ok": True}

        elif op == "signals":
            result = bus.signals(
                topic=req.get("topic"),
                agent=req.get("agent"),
                limit=req.get("limit", 50),
                since=req.get("since"),
            )
            return {"ok": True, "signals": result}

        elif op == "register":
            bus.register(req["agent_id"], metadata=req.get("metadata"))
            return {"ok": True}

        elif op == "agents":
            result = bus.agents()
            return {"ok": True, "agents": result}

        elif op == "transfer":
            result = bus.transfer(
                req["from_agent"],
                req["to_agent"],
                keys=req.get("keys"),
                namespace=req.get("namespace", "default"),
            )
            return {"ok": True, **result}

        elif op == "snapshot":
            result = bus.snapshot(namespace=req.get("namespace", "default"))
            return {"ok": True, "data": result}

        elif op == "restore":
            count = bus.restore(req["data"], namespace=req.get("namespace", "default"))
            return {"ok": True, "count": count}

        elif op == "ping":
            return {"ok": True, "pong": True}

        # ── Handoff Sessions ──

        elif op == "save_session":
            kwargs = {k: v for k, v in req.items() if k not in ("op", "agent_id")}
            sid = bus.save_session(req["agent_id"], **kwargs)
            return {"ok": True, "session_id": sid}

        elif op == "get_session":
            result = bus.get_session(
                session_id=req.get("session_id"),
                agent_id=req.get("agent_id"),
            )
            return {"ok": True, "session": result}

        elif op == "list_sessions":
            result = bus.list_sessions(
                agent_id=req.get("agent_id"),
                status=req.get("status"),
            )
            return {"ok": True, "sessions": result}

        elif op == "update_session":
            kwargs = {k: v for k, v in req.items() if k not in ("op", "session_id")}
            bus.update_session(req["session_id"], **kwargs)
            return {"ok": True}

        # ── Handoff Lanes ──

        elif op == "open_lane":
            lid = bus.open_lane(
                req["session_id"],
                req["from_agent"],
                req["to_agent"],
                context=req.get("context"),
            )
            return {"ok": True, "lane_id": lid}

        elif op == "get_lane":
            result = bus.get_lane(req["lane_id"])
            return {"ok": True, "lane": result}

        elif op == "list_lanes":
            result = bus.list_lanes(session_id=req.get("session_id"))
            return {"ok": True, "lanes": result}

        elif op == "close_lane":
            bus.close_lane(req["lane_id"])
            return {"ok": True}

        # ── Handoff Checkpoints ──

        elif op == "checkpoint":
            cid = bus.checkpoint(
                req["session_id"],
                req["agent_id"],
                req["snapshot"],
                lane_id=req.get("lane_id"),
            )
            return {"ok": True, "checkpoint_id": cid}

        elif op == "list_checkpoints":
            result = bus.list_checkpoints(
                session_id=req.get("session_id"),
                lane_id=req.get("lane_id"),
            )
            return {"ok": True, "checkpoints": result}

        else:
            return {"ok": False, "error": f"unknown op: {op}"}

    def _send(self, obj: Dict) -> None:
        data = json.dumps(obj) + "\n"
        with self._write_lock:
            try:
                self.wfile.write(data.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, OSError):
                pass


class _ThreadedTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class BusServer:
    """TCP server that wraps a Bus instance for cross-process access."""

    def __init__(self, bus: Any, host: str = "127.0.0.1", port: int = 9470) -> None:
        self._bus = bus
        self._host = host
        self._port = port
        self._server: Optional[_ThreadedTCPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._server = _ThreadedTCPServer(
            (self._host, self._port), _ClientHandler
        )
        self._server.bus = self._bus  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        # Update port in case 0 was requested (ephemeral)
        self._port = self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def address(self) -> Tuple[str, int]:
        return (self._host, self._port)


class BusClient:
    """TCP client that proxies Bus operations to a remote BusServer."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9470) -> None:
        self._host = host
        self._port = port
        self._sock = socket.create_connection((host, port))
        self._rfile = self._sock.makefile("rb")
        self._wlock = threading.Lock()
        self._rlock = threading.Lock()
        self._sub_callbacks: Dict[str, List[Callable]] = {}
        self._listener: Optional[threading.Thread] = None

    def _send(self, obj: Dict) -> Dict:
        data = json.dumps(obj) + "\n"
        with self._wlock:
            self._sock.sendall(data.encode("utf-8"))
        with self._rlock:
            while True:
                line = self._rfile.readline()
                if not line:
                    raise ConnectionError("Server closed connection")
                resp = json.loads(line.decode("utf-8"))
                # Skip push events (from subscriptions), queue them
                if "event" in resp:
                    self._dispatch_push(resp)
                    continue
                return resp

    def put(
        self,
        key: str,
        value: Any,
        agent: Optional[str] = None,
        namespace: str = "default",
    ) -> None:
        resp = self._send({
            "op": "put", "key": key, "value": value,
            "agent": agent, "namespace": namespace,
        })
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "put failed"))

    def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        resp = self._send({"op": "get", "key": key, "namespace": namespace})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "get failed"))
        return resp.get("value")

    def delete(self, key: str, namespace: str = "default") -> bool:
        resp = self._send({"op": "delete", "key": key, "namespace": namespace})
        return resp.get("deleted", False)

    def keys(self, namespace: str = "default", agent: Optional[str] = None) -> List[str]:
        resp = self._send({"op": "keys", "namespace": namespace, "agent": agent})
        return resp.get("keys", [])

    def all(self, namespace: str = "default") -> Dict[str, Any]:
        resp = self._send({"op": "all", "namespace": namespace})
        return resp.get("data", {})

    def clear(self, namespace: str = "default") -> int:
        resp = self._send({"op": "clear", "namespace": namespace})
        return resp.get("count", 0)

    def publish(
        self, topic: str, data: Any, agent: Optional[str] = None
    ) -> int:
        resp = self._send({"op": "publish", "topic": topic, "data": data, "agent": agent})
        return resp.get("count", 0)

    def _dispatch_push(self, event: Dict) -> None:
        """Handle a push event from a subscription."""
        topic = event.get("topic", "")
        data = event.get("data")
        agent = event.get("agent")
        for cb in self._sub_callbacks.get(topic, []):
            try:
                cb(topic, data, agent)
            except Exception:
                pass

    def subscribe(self, topic: str, callback: Callable) -> None:
        resp = self._send({"op": "subscribe", "topic": topic})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "subscribe failed"))
        if topic not in self._sub_callbacks:
            self._sub_callbacks[topic] = []
        self._sub_callbacks[topic].append(callback)

    def signals(
        self,
        topic: Optional[str] = None,
        agent: Optional[str] = None,
        limit: int = 50,
        since: Optional[str] = None,
    ) -> List[Dict]:
        resp = self._send({
            "op": "signals", "topic": topic, "agent": agent,
            "limit": limit, "since": since,
        })
        return resp.get("signals", [])

    def register(self, agent_id: str, metadata: Optional[Dict] = None) -> None:
        self._send({"op": "register", "agent_id": agent_id, "metadata": metadata})

    def agents(self) -> List[Dict]:
        resp = self._send({"op": "agents"})
        return resp.get("agents", [])

    def transfer(
        self,
        from_agent: str,
        to_agent: str,
        keys: Optional[List[str]] = None,
        namespace: str = "default",
    ) -> Dict:
        resp = self._send({
            "op": "transfer", "from_agent": from_agent, "to_agent": to_agent,
            "keys": keys, "namespace": namespace,
        })
        return {"transferred": resp.get("transferred", 0), "keys": resp.get("keys", [])}

    def snapshot(self, namespace: str = "default") -> Dict:
        resp = self._send({"op": "snapshot", "namespace": namespace})
        return resp.get("data", {})

    def restore(self, data: Dict, namespace: str = "default") -> int:
        resp = self._send({"op": "restore", "data": data, "namespace": namespace})
        return resp.get("count", 0)

    # ── Handoff Sessions ──

    def save_session(self, agent_id: str, **kwargs: Any) -> str:
        resp = self._send({"op": "save_session", "agent_id": agent_id, **kwargs})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "save_session failed"))
        return resp["session_id"]

    def get_session(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict]:
        resp = self._send({"op": "get_session", "session_id": session_id, "agent_id": agent_id})
        return resp.get("session")

    def list_sessions(
        self,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict]:
        resp = self._send({"op": "list_sessions", "agent_id": agent_id, "status": status})
        return resp.get("sessions", [])

    def update_session(self, session_id: str, **kwargs: Any) -> None:
        resp = self._send({"op": "update_session", "session_id": session_id, **kwargs})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "update_session failed"))

    # ── Handoff Lanes ──

    def open_lane(
        self,
        session_id: str,
        from_agent: str,
        to_agent: str,
        context: Optional[Dict] = None,
    ) -> str:
        resp = self._send({
            "op": "open_lane", "session_id": session_id,
            "from_agent": from_agent, "to_agent": to_agent,
            "context": context,
        })
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "open_lane failed"))
        return resp["lane_id"]

    def get_lane(self, lane_id: str) -> Optional[Dict]:
        resp = self._send({"op": "get_lane", "lane_id": lane_id})
        return resp.get("lane")

    def list_lanes(self, session_id: Optional[str] = None) -> List[Dict]:
        resp = self._send({"op": "list_lanes", "session_id": session_id})
        return resp.get("lanes", [])

    def close_lane(self, lane_id: str) -> None:
        resp = self._send({"op": "close_lane", "lane_id": lane_id})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "close_lane failed"))

    # ── Handoff Checkpoints ──

    def checkpoint(
        self,
        session_id: str,
        agent_id: str,
        snapshot: Dict,
        lane_id: Optional[str] = None,
    ) -> str:
        resp = self._send({
            "op": "checkpoint", "session_id": session_id,
            "agent_id": agent_id, "snapshot": snapshot,
            "lane_id": lane_id,
        })
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "checkpoint failed"))
        return resp["checkpoint_id"]

    def list_checkpoints(
        self,
        session_id: Optional[str] = None,
        lane_id: Optional[str] = None,
    ) -> List[Dict]:
        resp = self._send({"op": "list_checkpoints", "session_id": session_id, "lane_id": lane_id})
        return resp.get("checkpoints", [])

    def close(self) -> None:
        try:
            self._rfile.close()
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass


def main() -> None:
    """CLI entry point: `engram-bus` starts a server."""
    parser = argparse.ArgumentParser(description="engram-bus TCP server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9470, help="Bind port (default: 9470)")
    args = parser.parse_args()

    # Import here to avoid circular import at module level
    from engram_bus.bus import Bus

    bus = Bus(serve=False)
    server = BusServer(bus, host=args.host, port=args.port)
    server.start()
    host, port = server.address()
    print(f"engram-bus server listening on {host}:{port}")
    try:
        threading.Event().wait()  # Block forever
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.stop()
        bus.close()
