"""Monitor role management — tracks which agent is monitor, builds prompts.

The monitor is NOT an internal LLM. It is whichever registered agent
(claude-code, codex, a custom agent) the user assigns the "monitor" role to.
This module manages that role and builds context for the monitor and subagents.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Directive patterns parsed from monitor responses
_DELEGATE_RE = re.compile(r"@delegate\(\s*(\w[\w-]*)\s*,\s*(.+?)\s*\)", re.DOTALL)
_ASK_RE = re.compile(r"@ask\(\s*(\w[\w-]*)\s*,\s*(.+?)\s*\)", re.DOTALL)
_DECIDE_RE = re.compile(r"@decide\(\s*(.+?)\s*\)", re.DOTALL)


class MonitorRole:
    """Tracks which agent has the monitor role and builds context for it."""

    def __init__(self, memory: Any, bus: Any, warroom: Any) -> None:
        self._memory = memory
        self._bus = bus
        self._warroom = warroom

    def assign(self, room_id: str, agent_name: str) -> dict[str, Any]:
        """Assign an agent as monitor for a room. Changeable at any time."""
        return self._warroom.set_monitor(room_id, agent_name)

    def get_monitor(self, room_id: str) -> str | None:
        """Get current monitor agent name for a room."""
        room = self._warroom.get(room_id)
        if not room:
            return None
        monitor = room.get("wr_monitor_agent", "")
        return monitor or None

    def build_monitor_prompt(
        self,
        room_id: str,
        trigger_message: str | None = None,
    ) -> str:
        """Build a prompt for the monitor agent.

        Includes war room topic, agenda, participant list, recent messages,
        current state, and instructions for how to act as monitor.
        """
        room = self._warroom.get(room_id)
        if not room:
            return ""

        monitor_name = room.get("wr_monitor_agent", "monitor")
        context = self._warroom.get_context_for_agent(room_id, monitor_name)

        instructions = [
            "",
            "--- Monitor Instructions ---",
            "You are the MONITOR of this war room. Your responsibilities:",
            "1. Synthesize input from all participants",
            "2. Delegate tasks to specific agents when needed",
            "3. Drive toward a decision when ready",
            "",
            "Available directives (use these in your response):",
            "  @delegate(agent_name, instruction) — Send a task to a specific agent",
            "  @ask(agent_name, question) — Ask a specific agent a question",
            "  @decide(decision text) — Record a decision for the room",
            "  Plain text — Just a message to the war room",
        ]

        if trigger_message:
            instructions.append("")
            instructions.append(f"--- New Message ---")
            instructions.append(trigger_message)

        return context + "\n".join(instructions)

    def build_delegation_prompt(
        self,
        room_id: str,
        agent_name: str,
        instruction: str,
    ) -> str:
        """Build a prompt for a subagent being delegated to by the monitor.

        Includes war room context + specific instruction from monitor.
        """
        context = self._warroom.get_context_for_agent(room_id, agent_name)

        delegation = [
            "",
            "--- Delegation from Monitor ---",
            f"The monitor has asked you to: {instruction}",
            "",
            "Respond with your analysis/work. Your response will be posted",
            "back to the war room for all participants to see.",
        ]

        return context + "\n".join(delegation)

    def parse_monitor_response(self, response_text: str) -> list[dict[str, Any]]:
        """Parse monitor's response for actionable directives.

        Returns a list of parsed directives. A single response may contain
        multiple directives mixed with plain text.

        Each directive: {type: "message"|"delegate"|"decide"|"ask", ...}
        """
        directives: list[dict[str, Any]] = []

        # Extract all @delegate directives
        for match in _DELEGATE_RE.finditer(response_text):
            directives.append({
                "type": "delegate",
                "agent": match.group(1),
                "instruction": match.group(2).strip(),
            })

        # Extract all @ask directives
        for match in _ASK_RE.finditer(response_text):
            directives.append({
                "type": "ask",
                "agent": match.group(1),
                "question": match.group(2).strip(),
            })

        # Extract @decide directive (only first one)
        decide_match = _DECIDE_RE.search(response_text)
        if decide_match:
            directives.append({
                "type": "decide",
                "text": decide_match.group(1).strip(),
            })

        # Strip directives from text to get the plain message portion
        plain = response_text
        for pattern in (_DELEGATE_RE, _ASK_RE, _DECIDE_RE):
            plain = pattern.sub("", plain)
        plain = plain.strip()

        if plain:
            directives.insert(0, {"type": "message", "text": plain})

        # If no directives found at all, treat entire response as message
        if not directives:
            directives.append({"type": "message", "text": response_text})

        return directives
