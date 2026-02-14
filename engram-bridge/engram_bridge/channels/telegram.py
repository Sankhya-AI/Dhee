"""Telegram channel adapter using python-telegram-bot."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from engram_bridge.channels.base import BaseChannel, IncomingMessage
from engram_bridge.utils import split_message

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    """Telegram bot adapter — receives messages and sends responses."""

    def __init__(self, token: str, allowed_users: list[int]):
        self._token = token
        self._allowed_users = set(allowed_users) if allowed_users else set()
        self._app: Application | None = None
        self._on_message: Callable[[IncomingMessage], Awaitable[None]] | None = None

    async def start(self, on_message: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        self._on_message = on_message
        self._app = Application.builder().token(self._token).build()

        # Register commands
        for cmd in ("start", "switch", "status", "stop", "agents", "sessions", "memory"):
            self._app.add_handler(CommandHandler(cmd, self._handle_command))

        # Catch-all text → pipe to bridge
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram channel started. Listening for messages...")

    def _check_auth(self, user_id: int) -> bool:
        """Check if user is authorized. Empty allowed_users = allow all."""
        if not self._allowed_users:
            return True
        return user_id in self._allowed_users

    async def _handle_text(self, update: Update, context) -> None:
        """Handle non-command text messages."""
        if not update.effective_user or not update.message:
            return
        if not self._check_auth(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        msg = IncomingMessage(
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            text=update.message.text,
            username=update.effective_user.username or "",
            is_command=False,
            command=None,
            command_args=[],
        )
        await self._on_message(msg)

    async def _handle_command(self, update: Update, context) -> None:
        """Handle bot commands (/start, /switch, etc.)."""
        if not update.effective_user or not update.message:
            return
        if not self._check_auth(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        text = update.message.text or ""
        parts = text.split()
        command = parts[0].lstrip("/").split("@")[0]  # strip bot username suffix
        args = parts[1:] if len(parts) > 1 else []

        msg = IncomingMessage(
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            text=text,
            username=update.effective_user.username or "",
            is_command=True,
            command=command,
            command_args=args,
        )
        await self._on_message(msg)

    async def send_text(self, chat_id: int, text: str) -> int:
        """Send text, auto-splitting at Telegram's 4096 char limit."""
        chunks = split_message(text)
        msg_id = 0
        for chunk in chunks:
            sent = await self._app.bot.send_message(chat_id, chunk)
            if msg_id == 0:
                msg_id = sent.message_id
        return msg_id

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        """Edit a previously sent message."""
        try:
            await self._app.bot.edit_message_text(
                text[:4096], chat_id=chat_id, message_id=message_id
            )
        except Exception as e:
            # Telegram raises if message content is identical
            if "not modified" not in str(e).lower():
                logger.warning("Failed to edit message %d: %s", message_id, e)

    async def send_file(self, chat_id: int, content: bytes, filename: str) -> None:
        """Send a file as a document."""
        import io
        await self._app.bot.send_document(
            chat_id, document=io.BytesIO(content), filename=filename
        )

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None
