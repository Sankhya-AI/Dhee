"""Heartbeat — schedule and manage recurring agent behaviors."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from engram_heartbeat.behaviors import BUILTIN_BEHAVIORS, run_behavior
from engram_heartbeat.runner import HeartbeatRunner

logger = logging.getLogger(__name__)


class Heartbeat:
    """Manages scheduled proactive behaviors for an agent.

    Behaviors are stored as memories with memory_type="heartbeat" and run
    on a configurable interval by the HeartbeatRunner background thread.
    """

    def __init__(self, memory: Any, agent_id: str, bus: Any = None,
                 user_id: str = "system", tick_interval: float = 60.0) -> None:
        self._memory = memory
        self._agent_id = agent_id
        self._bus = bus
        self._user_id = user_id
        self._runner = HeartbeatRunner(self, tick_interval=tick_interval)

    # ── Helpers ──

    def _find_heartbeats(self) -> list[dict]:
        """Get all heartbeat memories for this agent."""
        results = self._memory.get_all(
            user_id=self._user_id,
            filters={"memory_type": "heartbeat", "hb_agent_id": self._agent_id},
            limit=100,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return items

    def _find_heartbeat(self, heartbeat_id: str) -> dict | None:
        """Find a specific heartbeat by ID."""
        try:
            mem = self._memory.get(heartbeat_id)
            if mem and mem.get("metadata", {}).get("memory_type") == "heartbeat":
                return mem
        except Exception:
            pass
        return None

    def _format_heartbeat(self, mem: dict) -> dict:
        """Format a raw memory into a heartbeat dict."""
        md = mem.get("metadata", {})
        return {
            "id": mem.get("id", ""),
            "agent_id": md.get("hb_agent_id", ""),
            "name": md.get("hb_name", ""),
            "action": md.get("hb_action", ""),
            "interval_minutes": md.get("hb_interval_minutes", 0),
            "params": md.get("hb_params", {}),
            "enabled": md.get("hb_enabled", True),
            "last_run": md.get("hb_last_run", ""),
            "next_run": md.get("hb_next_run", ""),
            "run_count": md.get("hb_run_count", 0),
            "created_at": md.get("hb_created_at", ""),
        }

    # ── Public API ──

    def schedule(self, *, name: str, action: str, interval_minutes: int,
                 params: dict | None = None, enabled: bool = True) -> dict:
        """Register a scheduled behavior. Stored as a memory."""
        now = datetime.now(timezone.utc)
        next_run = (now + timedelta(minutes=interval_minutes)).isoformat()

        content = f"Heartbeat: {name} ({action}) every {interval_minutes}min for {self._agent_id}"
        metadata = {
            "memory_type": "heartbeat",
            "hb_agent_id": self._agent_id,
            "hb_name": name,
            "hb_action": action,
            "hb_interval_minutes": interval_minutes,
            "hb_params": params or {},
            "hb_enabled": enabled,
            "hb_last_run": "",
            "hb_next_run": next_run,
            "hb_run_count": 0,
            "hb_created_at": now.isoformat(),
        }

        result = self._memory.add(
            content,
            user_id=self._user_id,
            metadata=metadata,
            categories=["heartbeats"],
            infer=False,
        )
        items = result.get("results", [])
        if items:
            return self._format_heartbeat(items[0])
        return {"name": name, "action": action, "interval_minutes": interval_minutes}

    def list(self) -> list[dict]:
        """List all scheduled behaviors for this agent."""
        mems = self._find_heartbeats()
        return [self._format_heartbeat(m) for m in mems]

    def remove(self, heartbeat_id: str) -> bool:
        """Remove a scheduled behavior."""
        try:
            self._memory.delete(heartbeat_id)
            return True
        except Exception:
            return False

    def enable(self, heartbeat_id: str) -> dict:
        """Enable a disabled heartbeat."""
        return self._set_enabled(heartbeat_id, True)

    def disable(self, heartbeat_id: str) -> dict:
        """Disable without removing."""
        return self._set_enabled(heartbeat_id, False)

    def _set_enabled(self, heartbeat_id: str, enabled: bool) -> dict:
        """Set the enabled state of a heartbeat."""
        mem = self._find_heartbeat(heartbeat_id)
        if not mem:
            return {"error": f"Heartbeat '{heartbeat_id}' not found"}
        md = dict(mem.get("metadata", {}))
        md["hb_enabled"] = enabled
        self._memory.update(heartbeat_id, {"metadata": md})
        updated = self._memory.get(heartbeat_id)
        return self._format_heartbeat(updated) if updated else self._format_heartbeat(mem)

    def start(self) -> None:
        """Start the background runner."""
        self._runner.start()

    def stop(self) -> None:
        """Stop the background runner."""
        self._runner.stop()

    def tick(self) -> list[dict]:
        """Manual tick — run any due behaviors now. Returns results."""
        now = datetime.now(timezone.utc)
        mems = self._find_heartbeats()
        results = []

        for mem in mems:
            md = mem.get("metadata", {})
            if not md.get("hb_enabled", True):
                continue

            next_run_str = md.get("hb_next_run", "")
            if next_run_str:
                try:
                    next_run = datetime.fromisoformat(next_run_str.replace("Z", "+00:00"))
                    if next_run > now:
                        continue
                except (ValueError, TypeError):
                    pass

            # Run the behavior
            action = md.get("hb_action", "")
            params = md.get("hb_params", {})
            result = run_behavior(action, self._memory, params,
                                  bus=self._bus, agent_id=self._agent_id)
            result["heartbeat_name"] = md.get("hb_name", "")
            results.append(result)

            # Update last_run, next_run, run_count
            interval = md.get("hb_interval_minutes", 60)
            new_md = dict(md)
            new_md["hb_last_run"] = now.isoformat()
            new_md["hb_next_run"] = (now + timedelta(minutes=interval)).isoformat()
            new_md["hb_run_count"] = md.get("hb_run_count", 0) + 1
            try:
                self._memory.update(mem["id"], {"metadata": new_md})
            except Exception as e:
                logger.warning("Failed to update heartbeat after run: %s", e)

        return results
