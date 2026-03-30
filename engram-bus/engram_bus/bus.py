"""Main Bus class — hybrid in-memory + SQLite-backed agent communication bus."""

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from engram_bus.pubsub import PubSub
from engram_bus.workspace import Workspace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Bus:
    """Lightweight real-time agent-to-agent communication bus.

    In-memory hot path for put/get with optional TTL expiry.
    SQLite-backed durable handoff sessions for agent coordination.

    Usage::

        bus = Bus()
        bus.put("status", "ready", agent="planner", ttl=300)
        bus.get("status")  # "ready"

    Or as a context manager::

        with Bus() as bus:
            sid = bus.save_session("agent-1", task_summary="refactor auth")
    """

    def __init__(
        self,
        serve: bool = False,
        connect: Optional[str] = None,
        port: int = 9470,
        db_path: Optional[str] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._pubsub = PubSub()

        # In-memory stores
        self._data: Dict[Tuple[str, str], Tuple[Any, Optional[str], Optional[float]]] = {}
        # (key, namespace) -> (value, agent_id, expires_at)
        self._agents: Dict[str, Dict] = {}
        # agent_id -> {agent_id, metadata, first_seen, last_seen}
        self._signals: List[Dict] = []
        self._transfers: List[Dict] = []

        # SQLite handoff store (lazy init if no db_path)
        self._store = None
        self._db_path = db_path
        if db_path is not None:
            from engram_bus.store import HandoffStore
            self._store = HandoffStore(db_path)

        self._server = None
        self._client = None

        if serve:
            from engram_bus.server import BusServer
            self._server = BusServer(self, port=port)
            self._server.start()

        if connect is not None:
            from engram_bus.server import BusClient
            host, p = connect.rsplit(":", 1)
            self._client = BusClient(host, int(p))

    def _ensure_store(self) -> "HandoffStore":  # noqa: F821
        """Lazy-init SQLite store on first handoff call."""
        if self._store is None:
            from engram_bus.store import HandoffStore
            self._store = HandoffStore(self._db_path or ":memory:")
        return self._store

    # ── Active Memory (with TTL) ──

    def put(
        self,
        key: str,
        value: Any,
        agent: Optional[str] = None,
        namespace: str = "default",
        ttl: Optional[int] = None,
    ) -> None:
        if self._client is not None:
            self._client.put(key, value, agent=agent, namespace=namespace)
            return
        expires_at = (time.monotonic() + ttl) if ttl else None
        with self._lock:
            self._data[(key, namespace)] = (value, agent, expires_at)
        if agent:
            self._touch_agent(agent)
        self._pubsub.publish(
            f"__bus__.put.{namespace}",
            {"key": key, "value": value, "agent": agent},
            agent_id=agent,
        )

    def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        if self._client is not None:
            return self._client.get(key, namespace=namespace)
        with self._lock:
            entry = self._data.get((key, namespace))
            if entry is None:
                return None
            value, _, expires_at = entry
            if expires_at is not None and time.monotonic() > expires_at:
                del self._data[(key, namespace)]
                return None
            return value

    def delete(self, key: str, namespace: str = "default") -> bool:
        with self._lock:
            return self._data.pop((key, namespace), None) is not None

    def keys(
        self, namespace: str = "default", agent: Optional[str] = None
    ) -> List[str]:
        now = time.monotonic()
        with self._lock:
            result = []
            expired = []
            for (k, ns), (_, aid, exp) in self._data.items():
                if ns != namespace:
                    continue
                if exp is not None and now > exp:
                    expired.append((k, ns))
                    continue
                if agent is not None and aid != agent:
                    continue
                result.append(k)
            for ek in expired:
                del self._data[ek]
            return result

    def all(self, namespace: str = "default") -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            result = {}
            expired = []
            for (k, ns), (v, _, exp) in self._data.items():
                if ns != namespace:
                    continue
                if exp is not None and now > exp:
                    expired.append((k, ns))
                    continue
                result[k] = v
            for ek in expired:
                del self._data[ek]
            return result

    def clear(self, namespace: str = "default") -> int:
        with self._lock:
            to_remove = [key for key in self._data if key[1] == namespace]
            for key in to_remove:
                del self._data[key]
            return len(to_remove)

    # ── Real-time Pub/Sub ──

    def publish(
        self, topic: str, data: Any, agent: Optional[str] = None
    ) -> int:
        count = self._pubsub.publish(topic, data, agent_id=agent)
        with self._lock:
            self._signals.append({
                "id": len(self._signals) + 1,
                "topic": topic,
                "data": data,
                "agent_id": agent,
                "timestamp": _now(),
            })
        return count

    def subscribe(
        self, topic: str, callback: Callable, agent: Optional[str] = None
    ) -> None:
        self._pubsub.subscribe(topic, callback, agent_id=agent)

    def unsubscribe(self, topic: str, callback: Callable) -> None:
        self._pubsub.unsubscribe(topic, callback)

    # ── Agent Registry ──

    def register(self, agent_id: str, metadata: Optional[Dict] = None) -> None:
        now = _now()
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id]["metadata"] = metadata or {}
                self._agents[agent_id]["last_seen"] = now
            else:
                self._agents[agent_id] = {
                    "agent_id": agent_id,
                    "metadata": metadata or {},
                    "first_seen": now,
                    "last_seen": now,
                }

    def agents(self) -> List[Dict]:
        with self._lock:
            return list(self._agents.values())

    def _touch_agent(self, agent_id: str) -> None:
        now = _now()
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id]["last_seen"] = now
            else:
                self._agents[agent_id] = {
                    "agent_id": agent_id,
                    "metadata": {},
                    "first_seen": now,
                    "last_seen": now,
                }

    # ── Transfer ──

    def transfer(
        self,
        from_agent: str,
        to_agent: str,
        keys: Optional[List[str]] = None,
        namespace: str = "default",
    ) -> Dict:
        with self._lock:
            if keys is None:
                keys = [
                    k for (k, ns), (_, aid, _) in self._data.items()
                    if ns == namespace and aid == from_agent
                ]
            transferred = []
            for key in keys:
                entry = self._data.get((key, namespace))
                if entry is not None:
                    self._data[(key, namespace)] = (entry[0], to_agent, entry[2])
                    transferred.append(key)
            if transferred:
                self._transfers.append({
                    "id": len(self._transfers) + 1,
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "keys": transferred,
                    "namespace": namespace,
                    "timestamp": _now(),
                })
        return {"transferred": len(transferred), "keys": transferred}

    def transfers(
        self, agent: Optional[str] = None, limit: int = 50
    ) -> List[Dict]:
        with self._lock:
            if agent is not None:
                result = [
                    t for t in self._transfers
                    if t["from_agent"] == agent or t["to_agent"] == agent
                ]
            else:
                result = list(self._transfers)
            return result[-limit:]

    # ── Signals (query history) ──

    def signals(
        self,
        topic: Optional[str] = None,
        agent: Optional[str] = None,
        limit: int = 50,
        since: Optional[str] = None,
    ) -> List[Dict]:
        with self._lock:
            result = self._signals
            if topic is not None:
                result = [s for s in result if s["topic"] == topic]
            if agent is not None:
                result = [s for s in result if s["agent_id"] == agent]
            if since is not None:
                result = [s for s in result if s["timestamp"] >= since]
            return result[-limit:]

    # ── Workspace ──

    def workspace(self, name: str) -> Workspace:
        return Workspace(self, name)

    # ── Snapshot / Restore ──

    def snapshot(self, namespace: str = "default") -> Dict:
        return self.all(namespace=namespace)

    def restore(self, data: Dict, namespace: str = "default") -> int:
        count = 0
        for key, value in data.items():
            self.put(key, value, namespace=namespace)
            count += 1
        return count

    # ── Handoff Sessions (SQLite-backed) ──

    def save_session(self, agent_id: str, **kwargs: Any) -> str:
        return self._ensure_store().save_session(agent_id, **kwargs)

    def get_session(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict]:
        return self._ensure_store().get_session(session_id=session_id, agent_id=agent_id)

    def list_sessions(
        self,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict]:
        return self._ensure_store().list_sessions(agent_id=agent_id, status=status)

    def update_session(self, session_id: str, **kwargs: Any) -> None:
        self._ensure_store().update_session(session_id, **kwargs)

    # ── Handoff Lanes (SQLite-backed) ──

    def open_lane(
        self,
        session_id: str,
        from_agent: str,
        to_agent: str,
        context: Optional[Dict] = None,
    ) -> str:
        return self._ensure_store().open_lane(session_id, from_agent, to_agent, context=context)

    def get_lane(self, lane_id: str) -> Optional[Dict]:
        return self._ensure_store().get_lane(lane_id)

    def list_lanes(self, session_id: Optional[str] = None) -> List[Dict]:
        return self._ensure_store().list_lanes(session_id=session_id)

    def close_lane(self, lane_id: str) -> None:
        self._ensure_store().close_lane(lane_id)

    # ── Handoff Checkpoints (SQLite-backed) ──

    def checkpoint(
        self,
        session_id: str,
        agent_id: str,
        snapshot: Dict,
        lane_id: Optional[str] = None,
    ) -> str:
        return self._ensure_store().checkpoint(session_id, agent_id, snapshot, lane_id=lane_id)

    def list_checkpoints(
        self,
        session_id: Optional[str] = None,
        lane_id: Optional[str] = None,
    ) -> List[Dict]:
        return self._ensure_store().list_checkpoints(session_id=session_id, lane_id=lane_id)

    # ── Lifecycle ──

    def close(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._store is not None:
            self._store.close()
            self._store = None

    def __enter__(self) -> "Bus":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
