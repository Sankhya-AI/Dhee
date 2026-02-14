"""Base agent interface for all agent adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class AgentMessage:
    """A single message/event from an agent."""
    type: str          # "text", "tool_use", "tool_result", "error", "rate_limited"
    content: str       # display text
    session_id: str    # for resume
    metadata: dict = field(default_factory=dict)  # tool name, file paths, etc.


class BaseAgent(ABC):
    """Abstract base for agent adapters (Claude Code, Codex, custom CLI)."""

    @abstractmethod
    async def send(
        self, message: str, cwd: str, session_id: str | None = None
    ) -> AsyncIterator[AgentMessage]:
        """Send message to agent, yield streaming responses."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the agent process."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent display name."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Whether the agent process is currently active."""
