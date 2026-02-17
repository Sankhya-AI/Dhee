"""MCP tool definitions for engram-warroom."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools(server: Any, memory: Any, **kwargs: Any) -> None:
    """Register war room MCP tools on the given server."""
    from engram_warroom.warroom import WarRoom
    from engram_warroom.monitor import MonitorRole
    from engram_warroom.autopick import AutoPicker

    bus = kwargs.get("bus")
    warroom = WarRoom(memory, bus)
    monitor_role = MonitorRole(memory, bus, warroom)

    router = kwargs.get("router")
    auto_picker = AutoPicker(memory, bus, router=router)

    tool_defs = {
        "create_warroom": {
            "description": "Create a war room for multi-agent deliberation on a topic",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "War room topic / question to resolve"},
                    "agenda": {"type": "string", "description": "Agenda or context for the discussion"},
                    "task_id": {"type": "string", "description": "Optional linked task ID"},
                    "participants": {
                        "type": "array", "items": {"type": "string"},
                        "description": "List of agent names to participate",
                    },
                    "monitor_agent": {"type": "string", "description": "Agent name to act as monitor"},
                },
                "required": ["topic"],
            },
        },
        "warroom_message": {
            "description": "Post a message to a war room",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "room_id": {"type": "string", "description": "War room ID"},
                    "sender": {"type": "string", "description": "Who is sending the message"},
                    "content": {"type": "string", "description": "Message content"},
                    "message_type": {
                        "type": "string",
                        "description": "Type: message, proposal, vote, decision, action_item, system",
                        "default": "message",
                    },
                },
                "required": ["room_id", "sender", "content"],
            },
        },
        "warroom_messages": {
            "description": "Get messages from a war room",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "room_id": {"type": "string", "description": "War room ID"},
                    "limit": {"type": "integer", "description": "Max messages to return", "default": 50},
                    "since": {"type": "string", "description": "Only messages after this ISO timestamp"},
                },
                "required": ["room_id"],
            },
        },
        "warroom_transition": {
            "description": "Transition war room state (open -> discussing -> deciding -> decided -> delivering -> closed)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "room_id": {"type": "string", "description": "War room ID"},
                    "new_state": {
                        "type": "string",
                        "description": "Target state",
                        "enum": ["open", "discussing", "deciding", "decided", "delivering", "closed"],
                    },
                    "by": {"type": "string", "description": "Who initiated the transition"},
                },
                "required": ["room_id", "new_state"],
            },
        },
        "set_warroom_monitor": {
            "description": "Assign any agent as monitor for a war room (changeable anytime)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "room_id": {"type": "string", "description": "War room ID"},
                    "agent_name": {"type": "string", "description": "Agent to assign as monitor"},
                },
                "required": ["room_id", "agent_name"],
            },
        },
        "warroom_decide": {
            "description": "Record a decision in the war room",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "room_id": {"type": "string", "description": "War room ID"},
                    "decision_text": {"type": "string", "description": "The decision text"},
                    "action_items": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Action items resulting from the decision",
                    },
                },
                "required": ["room_id", "decision_text"],
            },
        },
        "list_warrooms": {
            "description": "List active (non-closed) war rooms",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        "auto_pick_task": {
            "description": "Pick and dispatch the highest priority pending task to an agent",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User scope for tasks", "default": "bridge"},
                    "agent_name": {"type": "string", "description": "Force dispatch to this agent"},
                },
            },
        },
        "close_warroom": {
            "description": "Close a war room with an optional summary",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "room_id": {"type": "string", "description": "War room ID"},
                    "summary": {"type": "string", "description": "Closing summary"},
                },
                "required": ["room_id"],
            },
        },
    }

    def _handle(name: str, args: dict) -> Any:
        if name == "create_warroom":
            return warroom.create(
                topic=args["topic"],
                agenda=args.get("agenda", ""),
                task_id=args.get("task_id", ""),
                participants=args.get("participants"),
                monitor_agent=args.get("monitor_agent", ""),
            )
        elif name == "warroom_message":
            return warroom.post_message(
                room_id=args["room_id"],
                sender=args["sender"],
                content=args["content"],
                message_type=args.get("message_type", "message"),
            )
        elif name == "warroom_messages":
            return warroom.get_messages(
                room_id=args["room_id"],
                since=args.get("since", ""),
                limit=args.get("limit", 50),
            )
        elif name == "warroom_transition":
            return warroom.transition(
                room_id=args["room_id"],
                new_state=args["new_state"],
                by=args.get("by", ""),
            )
        elif name == "set_warroom_monitor":
            return monitor_role.assign(
                room_id=args["room_id"],
                agent_name=args["agent_name"],
            )
        elif name == "warroom_decide":
            return warroom.set_decision(
                room_id=args["room_id"],
                decision_text=args["decision_text"],
                action_items=args.get("action_items"),
            )
        elif name == "list_warrooms":
            return warroom.list_active()
        elif name == "auto_pick_task":
            return auto_picker.pick_and_dispatch(
                user_id=args.get("user_id", "bridge"),
                agent_name=args.get("agent_name"),
            ) or {"message": "No pending tasks found"}
        elif name == "close_warroom":
            return warroom.close(
                room_id=args["room_id"],
                summary=args.get("summary", ""),
            )
        return {"error": f"Unknown tool: {name}"}

    if not hasattr(server, "_warroom_tools"):
        server._warroom_tools = {}
        server._warroom_handler = {}
    server._warroom_tools.update(tool_defs)
    server._warroom_handler = _handle
