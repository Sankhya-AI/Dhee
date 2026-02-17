"""War room CRUD and message store backed by Engram memory."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from engram_warroom.decision import DecisionState, validate_transition

logger = logging.getLogger(__name__)


class WarRoom:
    """War room lifecycle — create, message, transition, decide, close.

    Uses Engram memory for persistence:
    - Room metadata stored as memory_type="warroom"
    - Individual messages stored as memory_type="warroom_message"
    """

    def __init__(self, memory: Any, bus: Any) -> None:
        self._memory = memory
        self._bus = bus
        self._sequence_counters: dict[str, int] = {}

    # ── Room CRUD ──

    def create(
        self,
        topic: str,
        agenda: str = "",
        task_id: str = "",
        participants: list[str] | None = None,
        monitor_agent: str = "",
        created_by: str = "user",
    ) -> dict[str, Any]:
        """Create a new war room and return its metadata."""
        room_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        participants = participants or []

        content = topic
        if agenda:
            content += f"\n{agenda}"

        metadata = {
            "memory_type": "warroom",
            "wr_topic": topic,
            "wr_agenda": agenda,
            "wr_state": DecisionState.OPEN.value,
            "wr_task_id": task_id,
            "wr_monitor_agent": monitor_agent,
            "wr_participants": participants,
            "wr_decision_text": "",
            "wr_action_items": [],
            "wr_created_by": created_by,
            "wr_created_at": now,
            "wr_message_count": 0,
        }

        self._memory.add(
            content,
            user_id="warroom",
            metadata=metadata,
            memory_id=room_id,
            infer=False,
        )
        self._sequence_counters[room_id] = 0

        result = {"id": room_id, **metadata}

        if self._bus:
            self._bus.publish("warroom.created", result)

        logger.info("War room created: %s — %s", room_id, topic)
        return result

    def get(self, room_id: str) -> dict[str, Any] | None:
        """Get war room metadata by ID."""
        mem = self._memory.get(room_id)
        if not mem:
            return None
        md = mem.get("metadata", {})
        if md.get("memory_type") != "warroom":
            return None
        return {"id": room_id, **md, "content": mem.get("memory", mem.get("content", ""))}

    def list_active(self) -> list[dict[str, Any]]:
        """List all non-closed war rooms."""
        results = self._memory.get_all(
            user_id="warroom",
            filters={"memory_type": "warroom"},
            limit=50,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        rooms = []
        for item in items:
            md = item.get("metadata", {})
            if md.get("wr_state") != DecisionState.CLOSED.value:
                rooms.append({
                    "id": item.get("id", ""),
                    **md,
                    "content": item.get("memory", item.get("content", "")),
                })
        rooms.sort(key=lambda r: r.get("wr_created_at", ""), reverse=True)
        return rooms

    # ── Messages ──

    def post_message(
        self,
        room_id: str,
        sender: str,
        content: str,
        message_type: str = "message",
    ) -> dict[str, Any]:
        """Post a message to a war room."""
        msg_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        seq = self._sequence_counters.get(room_id, 0) + 1
        self._sequence_counters[room_id] = seq

        metadata = {
            "memory_type": "warroom_message",
            "wrmsg_room_id": room_id,
            "wrmsg_sender": sender,
            "wrmsg_message_type": message_type,
            "wrmsg_sequence": seq,
            "wrmsg_timestamp": now,
        }

        self._memory.add(
            content,
            user_id="warroom",
            metadata=metadata,
            memory_id=msg_id,
            infer=False,
        )

        # Update room message count
        room = self._memory.get(room_id)
        if room:
            room_md = room.get("metadata", {})
            room_md["wr_message_count"] = room_md.get("wr_message_count", 0) + 1
            self._memory.db.update_memory(room_id, {"metadata": room_md})

        result = {"id": msg_id, "content": content, **metadata}

        if self._bus:
            self._bus.publish("warroom.message", result)

        return result

    def get_messages(
        self,
        room_id: str,
        since: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve messages for a room, sorted by sequence."""
        results = self._memory.get_all(
            user_id="warroom",
            filters={"memory_type": "warroom_message"},
            limit=200,
        )
        items = results.get("results", []) if isinstance(results, dict) else results

        messages = []
        for item in items:
            md = item.get("metadata", {})
            if md.get("wrmsg_room_id") != room_id:
                continue
            if since and md.get("wrmsg_timestamp", "") <= since:
                continue
            messages.append({
                "id": item.get("id", ""),
                "content": item.get("memory", item.get("content", "")),
                **md,
            })

        messages.sort(key=lambda m: m.get("wrmsg_sequence", 0))
        return messages[-limit:]

    def get_context_for_agent(self, room_id: str, agent_name: str) -> str:
        """Build a text summary of the war room state + recent messages for agent prompt injection."""
        room = self.get(room_id)
        if not room:
            return ""

        lines = [
            f"=== WAR ROOM: {room.get('wr_topic', '')} ===",
            f"State: {room.get('wr_state', 'open')}",
            f"Participants: {', '.join(room.get('wr_participants', []))}",
            f"Monitor: {room.get('wr_monitor_agent', 'none')}",
        ]

        agenda = room.get("wr_agenda", "")
        if agenda:
            lines.append(f"Agenda: {agenda}")

        decision = room.get("wr_decision_text", "")
        if decision:
            lines.append(f"Decision: {decision}")

        lines.append("")
        lines.append("--- Recent Messages ---")

        messages = self.get_messages(room_id, limit=20)
        for msg in messages:
            sender = msg.get("wrmsg_sender", "?")
            mtype = msg.get("wrmsg_message_type", "message")
            content = msg.get("content", "")
            prefix = f"[{sender}]"
            if mtype != "message":
                prefix = f"[{sender}/{mtype}]"
            lines.append(f"{prefix} {content}")

        lines.append(f"\nYou are: {agent_name}")
        return "\n".join(lines)

    # ── State Transitions ──

    def transition(self, room_id: str, new_state: str, by: str = "") -> dict[str, Any]:
        """Validate and apply a state change."""
        room = self.get(room_id)
        if not room:
            return {"error": f"War room '{room_id}' not found"}

        current = room.get("wr_state", "open")
        if not validate_transition(current, new_state):
            return {
                "error": f"Invalid transition: {current} -> {new_state}",
                "current_state": current,
            }

        mem = self._memory.get(room_id)
        if mem:
            md = mem.get("metadata", {})
            md["wr_state"] = new_state
            self._memory.db.update_memory(room_id, {"metadata": md})

        result = {"room_id": room_id, "from_state": current, "to_state": new_state, "by": by}
        if self._bus:
            self._bus.publish("warroom.state_changed", result)

        logger.info("War room %s: %s -> %s (by %s)", room_id, current, new_state, by)
        return result

    def set_decision(
        self,
        room_id: str,
        decision_text: str,
        action_items: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record decision and transition to DECIDED."""
        room = self.get(room_id)
        if not room:
            return {"error": f"War room '{room_id}' not found"}

        mem = self._memory.get(room_id)
        if mem:
            md = mem.get("metadata", {})
            md["wr_decision_text"] = decision_text
            md["wr_action_items"] = action_items or []
            md["wr_state"] = DecisionState.DECIDED.value
            self._memory.db.update_memory(room_id, {"metadata": md})

        result = {
            "room_id": room_id,
            "decision": decision_text,
            "action_items": action_items or [],
            "state": DecisionState.DECIDED.value,
        }

        # Post as a decision message
        self.post_message(room_id, "system", f"DECISION: {decision_text}", "decision")

        if self._bus:
            self._bus.publish("warroom.decided", result)

        logger.info("War room %s decided: %s", room_id, decision_text[:100])
        return result

    def set_monitor(self, room_id: str, agent_name: str) -> dict[str, Any]:
        """Change the monitor agent for a room at any time."""
        room = self.get(room_id)
        if not room:
            return {"error": f"War room '{room_id}' not found"}

        old_monitor = room.get("wr_monitor_agent", "")
        mem = self._memory.get(room_id)
        if mem:
            md = mem.get("metadata", {})
            md["wr_monitor_agent"] = agent_name
            self._memory.db.update_memory(room_id, {"metadata": md})

        result = {"room_id": room_id, "old_monitor": old_monitor, "new_monitor": agent_name}

        self.post_message(
            room_id, "system",
            f"Monitor changed: {old_monitor or 'none'} -> {agent_name}",
            "system",
        )

        if self._bus:
            self._bus.publish("warroom.monitor_changed", result)

        return result

    def close(self, room_id: str, summary: str = "") -> dict[str, Any]:
        """Close a war room."""
        result = self.transition(room_id, DecisionState.CLOSED.value, by="system")
        if "error" in result:
            # Force-close: any state can close
            mem = self._memory.get(room_id)
            if mem:
                md = mem.get("metadata", {})
                old_state = md.get("wr_state", "open")
                md["wr_state"] = DecisionState.CLOSED.value
                self._memory.db.update_memory(room_id, {"metadata": md})
                result = {"room_id": room_id, "from_state": old_state, "to_state": "closed", "by": "system"}

        if summary:
            self.post_message(room_id, "system", f"Room closed: {summary}", "system")

        if self._bus:
            self._bus.publish("warroom.closed", {"room_id": room_id, "summary": summary})

        logger.info("War room %s closed", room_id)
        return result
