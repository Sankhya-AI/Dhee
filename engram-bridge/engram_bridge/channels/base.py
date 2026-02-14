"""Base channel interface for messaging platform adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class IncomingMessage:
    """A message received from a channel (Telegram, Discord, etc.)."""
    user_id: int
    chat_id: int
    text: str
    username: str
    is_command: bool
    command: str | None = None         # "start", "switch", "status", etc.
    command_args: list[str] = field(default_factory=list)
    metadata: dict | None = None       # Extra data (e.g. task_id for web channel)


class BaseChannel(ABC):
    """Abstract base for messaging platform adapters."""

    @abstractmethod
    async def start(self, on_message: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        """Start listening for messages. Calls on_message for each incoming message."""

    @abstractmethod
    async def send_text(self, chat_id: int, text: str) -> int:
        """Send text to a chat. Returns the message_id."""

    @abstractmethod
    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        """Edit a previously sent message."""

    @abstractmethod
    async def send_file(self, chat_id: int, content: bytes, filename: str) -> None:
        """Send a file to a chat."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel listener."""
